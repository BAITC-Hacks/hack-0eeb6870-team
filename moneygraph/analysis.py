"""Deterministic batch analysis. No LLM is involved in roles or exports."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from numbers import Integral
from pathlib import Path

import networkx as nx
import numpy as np
import pyarrow.parquet as pq

ROLES = {
    "consolidator": "Консолидация", "transit": "Транзит",
    "distributor": "Распределение", "terminal": "Конечный получатель",
    "coordinator": "Координирующий узел", "peripheral": "Периферия",
}
COLORS = {"consolidator": "#c37626", "transit": "#438cc7", "distributor": "#8b71c9",
          "terminal": "#439078", "coordinator": "#cb5867", "peripheral": "#9aa9ab", "boundary": "#b5a487"}
NODE_FIELDS = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence",
               "in_deg", "out_deg", "in_kzt", "out_kzt", "in_tx", "out_tx", "pagerank",
               "pass_through", "depth", "is_seed", "truncated_by_depth", "seed_reach", "fast_ratio"]
CLUSTER_FIELDS = ["cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"]
TOP_FIELDS = ["rank", "gid", "role", "priority_score", "why"]
LIMITS = "Наблюдаются только внутрибанковские переводы ≥5 000 KZT за период выгрузки; входящие потоки неполны. Роль — гипотеза для проверки, не вывод о виновности."
PRIORITY_WEIGHTS = {"volume": .25, "seed_reach": .25, "activity": .10, "bridge": .20, "role": .20}


def integer(v, label):
    if isinstance(v, bool) or not isinstance(v, (Integral, str)):
        raise ValueError(f"{label}: нужен целый идентификатор/счётчик")
    try:
        n = int(v)
        if str(v).strip() != str(n) and v != n:
            raise ValueError()
        if not -(2**63) <= n < 2**63:
            raise ValueError()
        return n
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"{label}: некорректное целое число") from None


def amount(v):
    try:
        n = float(v)
    except (ValueError, TypeError):
        raise ValueError("sum_kzt: требуется число") from None
    if not math.isfinite(n) or n <= 0:
        raise ValueError("sum_kzt: требуется конечное положительное число")
    return n


def date_value(v):
    try:
        d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise ValueError("date: ожидается дата ISO 8601 или Parquet timestamp") from None


def load_tables(folder):
    folder = Path(folder)
    tables = {}
    for name in ("nodes", "edges", "transactions"):
        file = folder / f"{name}.parquet"
        if not file.is_file():
            raise ValueError(f"Не найден {file.name} в {folder}")
        tables[name] = pq.read_table(file).to_pylist()
    return tables


def normalize(tables):
    required = {"nodes": {"gid", "depth", "is_seed"},
                "edges": {"src", "dst", "sum_kzt", "n_tx", "depth"},
                "transactions": {"src", "dst", "date", "sum_kzt"}}
    for name, fields in required.items():
        if name not in tables or not isinstance(tables[name], list):
            raise ValueError(f"Нет таблицы {name}")
        if name == "nodes" and not tables[name]:
            raise ValueError("Таблица nodes пуста")
        for row in tables[name]:
            if not fields.issubset(row):
                raise ValueError(f"{name}: отсутствуют поля {sorted(fields - row.keys())}")
    nodes = {}
    for r in tables["nodes"]:
        gid, depth = integer(r["gid"], "gid"), integer(r["depth"], "depth")
        if gid in nodes:
            raise ValueError(f"Дублируется gid={gid}")
        if depth not in range(5) or r["is_seed"] not in (True, False, 0, 1):
            raise ValueError("depth должен быть 0–4, is_seed — bool или 0/1")
        if bool(r["is_seed"]) != (depth == 0):
            raise ValueError(f"gid={gid}: is_seed не согласован с depth=0")
        nodes[gid] = {"gid": gid, "depth": depth, "is_seed": bool(r["is_seed"])}
    edges = {}
    for r in tables["edges"]:
        a, b = integer(r["src"], "src"), integer(r["dst"], "dst")
        if a not in nodes or b not in nodes:
            raise ValueError("В edges есть gid, отсутствующий в nodes")
        if (a, b) in edges:
            raise ValueError("edges должен содержать одну агрегированную строку на пару src,dst")
        nt = integer(r["n_tx"], "n_tx")
        dep = integer(r["depth"], "depth")
        if nt <= 0 or not 1 <= dep <= 4:
            raise ValueError("Некорректные n_tx/depth в edges")
        edges[a, b] = {"src": a, "dst": b, "sum_kzt": amount(r["sum_kzt"]), "n_tx": nt, "depth": dep}
    tx = []
    totals = defaultdict(lambda: [0., 0])
    for r in tables["transactions"]:
        a, b = integer(r["src"], "src"), integer(r["dst"], "dst")
        if a not in nodes or b not in nodes:
            raise ValueError("В transactions есть gid, отсутствующий в nodes")
        row = {"src": a, "dst": b, "sum_kzt": amount(r["sum_kzt"]), "date": date_value(r["date"])}
        tx.append(row)
        totals[a, b][0] += row["sum_kzt"]
        totals[a, b][1] += 1
    if set(totals) != set(edges):
        raise ValueError("Пары src,dst в edges и transactions не совпадают")
    for pair, (s, n) in totals.items():
        if not math.isclose(s, edges[pair]["sum_kzt"], abs_tol=.02, rel_tol=1e-9) or n != edges[pair]["n_tx"]:
            raise ValueError(f"edges и transactions не согласованы для {pair}")
    # The contract defines depth as the shortest directed distance from ANY seed.
    # Checking only the range would silently turn a first-hop node into a boundary.
    adjacency = defaultdict(list)
    for a, b in edges:
        adjacency[a].append(b)
    distances = {gid: 0 for gid, node in nodes.items() if node["is_seed"]}
    queue = deque(distances)
    while queue:
        source = queue.popleft()
        for target in adjacency[source]:
            if target not in distances:
                distances[target] = distances[source] + 1
                queue.append(target)
    for gid, node in nodes.items():
        if distances.get(gid) != node["depth"]:
            observed = distances.get(gid, "недостижим от seed")
            raise ValueError(f"gid={gid}: depth={node['depth']} не совпадает с минимальным коленом ({observed})")
    for (a, b), edge in edges.items():
        expected = nodes[a]["depth"] + 1
        if edge["depth"] != expected:
            raise ValueError(f"edges {a}→{b}: depth={edge['depth']}, ожидается колено обнаружения {expected}")
    return nodes, edges, sorted(tx, key=lambda t: (t["date"], t["src"], t["dst"], t["sum_kzt"]))


def temporal_features(tx):
    """FIFO matching is a temporal association, never a claim of fund provenance.

    Date-only records: outgoing on the same day is processed first; only later
    days are matched. Timestamp records can match within a day if ordered.
    """
    events = defaultdict(list)
    for t in tx:
        if t["src"] == t["dst"]:
            continue
        stamp = t["date"].timestamp()
        events[t["src"]].append((stamp, 0, t["sum_kzt"]))
        events[t["dst"]].append((stamp, 1, t["sum_kzt"]))
    matched = defaultdict(float)
    for gid, rows in events.items():
        pending = deque()
        for stamp, incoming, value in sorted(rows):
            while pending and stamp - pending[0][0] > 172800:
                pending.popleft()
            if incoming:
                pending.append([stamp, value])
                continue
            while pending and value > .0001:
                consumed = min(value, pending[0][1])
                matched[gid] += consumed
                pending[0][1] -= consumed
                value -= consumed
                if pending[0][1] <= .0001:
                    pending.popleft()
    return matched


def weighted_pagerank(g, alpha=.85, tol=1e-10, max_iter=1000):
    """Weighted PageRank on edge-bearing nodes, matching starter's graph scope.

    Vectorised power iteration avoids a SciPy dependency. Isolated nodes get 0.
    """
    active = sorted(v for v in g if g.degree(v) > 0)
    output = dict.fromkeys(g, 0.)
    if not active:
        return output
    index = {v: i for i, v in enumerate(active)}
    src, dst, weights = [], [], []
    for a, b, e in g.edges(data=True):
        src.append(index[a]); dst.append(index[b]); weights.append(e["weight"])
    src, dst, weights = np.array(src), np.array(dst), np.array(weights, dtype=float)
    n = len(active)
    totals = np.bincount(src, weights=weights, minlength=n)
    probabilities = weights / totals[src]
    dangling = totals == 0
    rank = np.full(n, 1. / n)
    for _ in range(max_iter):
        updated = np.full(n, (1 - alpha) / n + alpha * rank[dangling].sum() / n)
        np.add.at(updated, dst, alpha * rank[src] * probabilities)
        if np.abs(updated - rank).sum() < n * tol:
            output.update({v: float(updated[index[v]]) for v in active})
            return output
        rank = updated
    raise ValueError("PageRank не сошёлся за 1000 итераций")


def role_rules(m):
    """Evaluate the same canonical conditions used by classification and the UI."""
    labels = {"depth": "Колено", "out_degree": "Получателей", "in_degree": "Отправителей",
              "neighbor_clusters": "Кластеров среди соседей", "seed_reach": "Достижим от seed",
              "is_seed": "Исходный seed", "pass_ratio": "Отдал / получил"}

    def readable(value):
        if value is None:
            return "не определено"
        if isinstance(value, bool):
            return "да" if value else "нет"
        return f"{value:.6g}" if isinstance(value, float) else str(value)

    def condition(metric, op, threshold, expression=None):
        actual = m[metric]
        comparisons = {"==": lambda: actual == threshold, ">=": lambda: actual >= threshold,
                       "<=": lambda: actual <= threshold, ">": lambda: actual > threshold,
                       "<": lambda: actual < threshold}
        passed = actual is not None and bool(comparisons[op]())
        result = {"metric": metric, "label": labels[metric], "actual": actual, "operator": op,
                  "threshold": threshold, "passed": passed,
                  "explanation": f"{labels[metric]}: {readable(actual)}; условие {op} {readable(threshold)}"}
        if expression:
            result["threshold_expression"] = expression
            result["explanation"] += f" ({expression})"
        return result

    definitions = [
        ("boundary", "Граница наблюдения", "Колено = 4 и получателей = 0. Финансовая роль неизвестна.",
         [condition("depth", "==", 4), condition("out_degree", "==", 0)],
         "Отсутствие исходящих объясняется окончанием обхода; это не доказательство удержания средств."),
        ("coordinator", ROLES["coordinator"],
         "Не менее 3 кластеров среди соседей, достижим от не менее 3 seed, не менее 2 отправителей и 2 получателей.",
         [condition("neighbor_clusters", ">=", 3), condition("seed_reach", ">=", 3),
          condition("in_degree", ">=", 2), condition("out_degree", ">=", 2)],
         "Это структурная гипотеза: связи между сообществами не доказывают управление другими клиентами."),
        ("distributor", ROLES["distributor"],
         "Получателей не менее 8 и не менее 2 × max(число отправителей, 1).",
         [condition("out_degree", ">=", 8), condition("out_degree", ">=", 2 * max(m["in_degree"], 1),
                                                       "2 × max(число отправителей, 1)")],
         "Веерные переводы — наблюдаемый паттерн, но назначение платежей неизвестно."),
        ("consolidator", ROLES["consolidator"],
         "Не seed; отправителей не менее 4; отдал / получил не больше 0,35.",
         [condition("is_seed", "==", False), condition("in_degree", ">=", 4), condition("pass_ratio", "<=", .35)],
         "Остатки и внешние потоки неизвестны; низкая наблюдаемая доля исходящих не доказывает накопление на счёте."),
        ("transit", ROLES["transit"],
         "Не seed; есть отправители и получатели; отдал / получил от 0,8 до 1,2 включительно.",
         [condition("is_seed", "==", False), condition("in_degree", ">", 0), condition("out_degree", ">", 0),
          condition("pass_ratio", ">=", .8), condition("pass_ratio", "<=", 1.2)],
         "Роль основана на суммах за период. FIFO за 48 часов — отдельная поддержка; без неё сквозной транзит не подтверждён."),
        ("terminal", ROLES["terminal"], "Не seed; отправителей больше 0; получателей = 0; колено меньше 4.",
         [condition("is_seed", "==", False), condition("in_degree", ">", 0), condition("out_degree", "==", 0),
          condition("depth", "<", 4)],
         "Видимых исходящих нет, но деньги могли уйти вне банка, периода или порога наблюдения."),
    ]
    rules = [{"rule_id": key, "label": label, "description": description, "precedence": order,
              "conditions": conditions, "limitation": limitation,
              "matched": all(c["passed"] for c in conditions)}
             for order, (key, label, description, conditions, limitation) in enumerate(definitions, 1)]
    rules.append({"rule_id": "peripheral", "label": ROLES["peripheral"], "precedence": 7,
                  "description": "Ни одно из правил с приоритетами 1–6 не выполнено; определённая финансовая роль не установлена.",
                  "conditions": [], "matched": not any(r["matched"] for r in rules),
                  "limitation": "Недостаток признаков роли не означает низкую значимость клиента."})
    return rules


def role_decision(m):
    rules = role_rules(m)
    selected = next(rule for rule in rules if rule["matched"])
    key = selected["rule_id"]
    role = "peripheral" if key == "boundary" else key
    i, o, ratio = m["in_degree"], m["out_degree"], m["pass_ratio"]
    if key == "boundary":
        score = 0.
    elif key == "coordinator":
        score = min(.85, .5 + .05 * m["neighbor_clusters"] + .02 * min(m["seed_reach"], 10))
    elif key == "distributor":
        score = min(.95, .6 + .35 * min(o / 30, 1))
    elif key == "consolidator":
        score = min(.9, .55 + .2 * min(i / 12, 1) + .15 * (1 - ratio))
    elif key == "transit":
        score = min(.95, .6 + .2 * (1 - abs(1 - ratio) / .2) + .15 * m["fast_ratio"])
    else:
        score = .5 if key == "terminal" else .2
    secondary = [] if key == "boundary" else [r["rule_id"] for r in rules
                                               if r["matched"] and r["rule_id"] not in (key, "peripheral", "boundary")]
    return role, score, {k: v for k, v in selected.items() if k != "matched"}, secondary


def choose_role(m):
    """Compatibility wrapper; the canonical conditions also drive explanations."""
    role, score, _, _ = role_decision(m)
    return role, score


def pct_ranks(values):
    # Average rank for equal nonzero values; zero always means no signal.
    positive = sorted(v for v in values.values() if v > 0)
    import bisect
    return {k: (0 if v <= 0 else (bisect.bisect_left(positive, v) + bisect.bisect_right(positive, v) + 1) / (2 * len(positive)))
            for k, v in values.items()}


def priority_sensitivity(rows, features):
    """One-at-a-time weight sensitivity; reuses metrics, never repeats graph work."""
    top_k = min(20, len(rows))
    baseline = {r["gid"] for r in rows[:top_k]}
    scenarios = []
    for changed in PRIORITY_WEIGHTS:
        for multiplier in (.8, 1.2):
            weights = dict(PRIORITY_WEIGHTS)
            weights[changed] *= multiplier
            total = sum(weights.values())
            weights = {name: weight / total for name, weight in weights.items()}
            scores = {gid: round(min(1., sum(round(weights[name] * value, 6)
                                                   for name, value in signal.items())), 6)
                      for gid, signal in features.items()}
            ranked = sorted(scores, key=lambda gid: (-scores[gid], gid))[:top_k]
            overlap = len(baseline.intersection(ranked))
            scenarios.append({"changed_weight": changed, "multiplier": multiplier,
                              "weights": weights, "top_k_overlap": overlap,
                              "overlap_ratio": round(overlap / top_k, 6) if top_k else 1.,
                              "entered_gids": sorted(set(ranked) - baseline),
                              "left_gids": sorted(baseline - set(ranked))})
    minimum = min(s["overlap_ratio"] for s in scenarios)
    mean = sum(s["overlap_ratio"] for s in scenarios) / len(scenarios)
    limitations = ("Меняется по одному весу на ±20% с нормировкой суммы весов до 1. "
                   "Проверяется состав топа, а не порядок внутри него, точность ролей, пороги, "
                   "выборка betweenness или устойчивость к пропущенным данным.")
    return {"top_k": top_k, "baseline_weights": PRIORITY_WEIGHTS.copy(), "scenarios": scenarios,
            "min_overlap_ratio": minimum, "mean_overlap_ratio": round(mean, 6),
            "summary": f"Чувствительность весов: в 10 сценариях ±20% сохраняется минимум {minimum:.0%} состава топ-{top_k}; среднее {mean:.0%}.",
            "limitations": limitations}


def cluster_hypothesis(members, rows_by_id, internal, incoming, outgoing, n_seed):
    counts = {role: sum(rows_by_id[v]["role"] == role for v in members) for role in ROLES}
    boundary = sum(rows_by_id[v]["truncated_by_depth"] for v in members)
    if internal + incoming + outgoing == 0:
        purpose = "Назначение не определено: наблюдаемых переводов нет"
    elif boundary == len(members):
        purpose = "Граница наблюдения: назначение и дальнейшее движение средств неизвестны"
    elif counts["consolidator"] and counts["distributor"]:
        purpose = "Кандидат на участок сбора и последующего распределения средств"
    elif counts["consolidator"]:
        purpose = "Кандидат на участок консолидации средств"
    elif counts["distributor"]:
        purpose = "Кандидат на участок веерного распределения средств"
    elif counts["transit"]:
        purpose = "Кандидат на промежуточный участок движения средств по соотношению потоков"
    elif counts["coordinator"]:
        purpose = "Кандидат на связующий участок между сообществами"
    elif counts["terminal"]:
        purpose = "Наблюдаемый участок получения средств; единый финансовый центр не установлен"
    else:
        purpose = "Финансовое назначение по текущим признакам не определено"
    support = "; ".join(f"{ROLES[role].lower()}: {count}" for role, count in counts.items() if count)
    hypothesis = (f"{purpose}. Узлов {len(members)}, seed {n_seed}; внутренний оборот {internal:,.2f} KZT; "
                  f"из других кластеров {incoming:,.2f}, в другие кластеры {outgoing:,.2f} KZT. "
                  f"Роли: {support}. Граница: {boundary}. Гипотеза по неполной выборке, не доказательство единой группы.")
    return hypothesis, counts, boundary


def analyze(tables, demo=False):
    start = time.perf_counter()
    nodes, edges, tx = normalize(tables)
    g = nx.DiGraph()
    g.add_nodes_from(sorted(nodes))  # Retain all isolated seeds.
    for pair, e in sorted(edges.items()):
        g.add_edge(*pair, weight=e["sum_kzt"], n_tx=e["n_tx"])
    undirected = nx.Graph()
    undirected.add_nodes_from(g)
    for (a, b), e in sorted(edges.items()):
        if a != b:
            previous = undirected.get_edge_data(a, b, {}).get("weight", 0)
            undirected.add_edge(a, b, weight=previous + e["sum_kzt"])
    active = undirected.subgraph([v for v in g if undirected.degree(v) > 0])
    communities = list(nx.community.louvain_communities(active, weight="weight", seed=42)) if active else []
    communities.extend({v} for v in g if undirected.degree(v) == 0)
    communities.sort(key=lambda c: (-len(c), min(c)))
    membership = {v: index for index, c in enumerate(communities) for v in c}
    seeds = [v for v in g if nodes[v]["is_seed"]]
    reached_by = defaultdict(set)
    for seed in seeds:
        for v in nx.single_source_shortest_path_length(g, seed, cutoff=4):
            if v != seed:
                reached_by[v].add(seed)
    # Structural intermediary score; weights are amounts, not distances.
    centrality = nx.betweenness_centrality(g, k=min(128, len(g)), normalized=True, seed=42)
    fast = temporal_features(tx)
    pagerank = weighted_pagerank(g)
    rows = []
    for gid in g:
        senders = [v for v in g.predecessors(gid) if v != gid]
        receivers = [v for v in g.successors(gid) if v != gid]
        si = sum(g[v][gid]["weight"] for v in senders)
        so = sum(g[gid][v]["weight"] for v in receivers)
        in_tx = sum(g[v][gid]["n_tx"] for v in senders)
        out_tx = sum(g[gid][v]["n_tx"] for v in receivers)
        m = dict(nodes[gid], in_degree=len(senders), out_degree=len(receivers),
                 sum_in=round(si, 2), sum_out=round(so, 2), pass_ratio=so / si if si else None,
                 fast_ratio=min(1., fast[gid] / si) if si else 0.,
                 fast_sum=round(fast[gid], 2), seed_reach=len(reached_by[gid]),
                 neighbor_clusters=len({membership[v] for v in senders + receivers}),
                 betweenness=centrality[gid], cluster_id=membership[gid],
                 in_tx=in_tx, out_tx=out_tx, pagerank=pagerank[gid],
                 in_deg=len(senders), out_deg=len(receivers), in_kzt=round(si, 2), out_kzt=round(so, 2),
                 pass_through=so / si if si else None,
                 truncated_by_depth=nodes[gid]["depth"] == 4 and len(receivers) == 0)
        role, score, rule, secondary = role_decision(m)
        temporal = {"status": "matched" if m["fast_sum"] > 0 else "not_observed", "window_hours": 48,
                    "matched_kzt": m["fast_sum"], "ratio": m["fast_ratio"]}
        temporal["note"] = (
            f"FIFO за 48 часов: сопоставлено {m['fast_sum']:,.2f} KZT ({m['fast_ratio']:.1%} входящего объёма). "
            "Это временная связь, не доказательство происхождения средств."
            if m["fast_sum"] > 0 else
            "Временная поддержка не наблюдается: FIFO за 48 часов сопоставил 0 KZT. "
            "При данных по дням переводы в один день не сопоставляются; это не доказывает отсутствие транзита."
        )
        reasons = {
            "boundary": "Граница 4-го колена: исходящие не наблюдаются; роль не определена",
            "coordinator": f"Связи с {m['neighbor_clusters']} кластерами; достижим от {m['seed_reach']} seed; гипотеза координации",
            "distributor": f"Получателей: {len(receivers)}, отправителей: {len(senders)}; признаки веерного распределения",
            "consolidator": f"Отправителей: {len(senders)}; наблюдаемая доля перевода дальше {100*(m['pass_ratio'] or 0):.1f}%; признаки консолидации",
            "transit": f"Отдал/получил в выборке: {(m['pass_ratio'] or 0):.2f}; временно сопоставлено за 48 ч: {m['fast_ratio']:.0%}; признаки транзита",
            "terminal": f"Входящих переводов: {in_tx}, отправителей: {len(senders)}, исходящих: 0; колено {m['depth']}; гипотеза конечного получателя",
            "peripheral": f"Отправителей: {len(senders)}, получателей: {len(receivers)}; переводов вход/выход: {in_tx}/{out_tx}; недостаточно признаков роли",
        }
        evidence = reasons["boundary"] if m["truncated_by_depth"] else reasons[role]
        if m["is_seed"]:
            evidence += "; входящие seed неполны"
        rows.append(dict(m, role=role, role_score=round(score, 4), evidence=evidence[:200],
                         role_rule=rule, secondary_roles=secondary, temporal_support=temporal))
    volume = pct_ranks({r["gid"]: r["sum_in"] + r["sum_out"] for r in rows})
    bridge = pct_ranks(centrality)
    seed_rank = pct_ranks({r["gid"]: r["seed_reach"] for r in rows})
    activity_rank = pct_ranks({r["gid"]: r["in_tx"] + r["out_tx"] for r in rows})
    priority_features = {}
    for r in rows:
        gid = r["gid"]
        signal = r["role_score"] if r["role"] not in ("peripheral", "boundary") else 0
        priority_features[gid] = {"volume": volume[gid], "seed_reach": seed_rank[gid],
                                  "activity": activity_rank[gid], "bridge": bridge[gid], "role": signal}
        factors = {name: round(PRIORITY_WEIGHTS[name] * value, 6)
                   for name, value in priority_features[gid].items()}
        r["priority_factors"] = factors
        r["priority_score"] = round(min(1., sum(factors.values())), 6)
        r["color"] = COLORS["boundary"] if r["truncated_by_depth"] else COLORS[r["role"]]
        r["why"] = f"{r['evidence']}. Достижим от {r['seed_reach']} seed; переводов {r['in_tx'] + r['out_tx']}; входящий+исходящий объём {r['sum_in'] + r['sum_out']:,.0f} KZT."
    rows.sort(key=lambda r: (-r["priority_score"], r["gid"]))
    by_id = {r["gid"]: r for r in rows}
    cluster_rows = []
    for cid, c in enumerate(communities):
        ranked = sorted(c, key=lambda v: (-by_id[v]["priority_score"], v))
        internal = sum(e["sum_kzt"] for (a, b), e in edges.items() if a in c and b in c)
        incoming = sum(e["sum_kzt"] for (a, b), e in edges.items() if a not in c and b in c)
        outgoing = sum(e["sum_kzt"] for (a, b), e in edges.items() if a in c and b not in c)
        n_seed = sum(nodes[v]["is_seed"] for v in c)
        hypothesis, role_counts, boundary = cluster_hypothesis(c, by_id, internal, incoming, outgoing, n_seed)
        cluster_rows.append({"cluster_id": cid, "n_nodes": len(c), "n_seed": n_seed,
                             "sum_kzt_internal": round(internal, 2), "top_gids": ";".join(map(str, ranked[:5])),
                             "hypothesis": hypothesis, "sum_kzt_incoming_external": round(incoming, 2),
                             "sum_kzt_outgoing_external": round(outgoing, 2), "role_counts": role_counts,
                             "n_boundary": boundary})
    # Deterministic layout, computed once. No external JavaScript/CDN dependency.
    positions = {}
    columns = max(1, math.ceil(math.sqrt(len(communities))))
    for cid, c in enumerate(communities):
        local = nx.spring_layout(undirected.subgraph(sorted(c)), seed=42, iterations=25, weight=None)
        scale = max(.28, min(1.5, math.sqrt(len(c)) / 5))
        for v, xy in local.items():
            positions[v] = [round((cid % columns) * 3.6 + float(xy[0]) * scale, 5),
                            round((cid // columns) * 3.6 + float(xy[1]) * scale, 5)]
    for r in rows:
        r["x"], r["y"] = positions[r["gid"]]
    warnings = []
    for count, note in [
        (sum(r["depth"] == 4 and r["out_degree"] == 0 for r in rows), "узлов на обрезанной границе; исходящий поток неизвестен"),
        (sum(r["sum_out"] > r["sum_in"] + .01 and r["sum_in"] > 0 for r in rows), "узла отдают больше полученного при ненулевом входящем потоке; это не полный баланс"),
        (sum(r["sum_out"] > 0 and r["sum_in"] == 0 for r in rows), "узла имеют исходящие без видимых входящих; отношение потоков не определено"),
        (sum(g.degree(v) == 0 and nodes[v]["is_seed"] for v in g), "изолированных seed сохранено в результатах"),
        (sum(a == b for a, b in edges), "самопереводов исключено из признаков ролей, но сохранено в обороте"),
        (sum(t["sum_kzt"] < 5000 for t in tx), "транзакций ниже заявленного порога 5 000 KZT; проверьте входные данные"),
    ]:
        if count:
            warnings.append(f"{count} {note}")
    canonical = {"nodes": [nodes[v] for v in sorted(nodes)], "edges": [edges[p] for p in sorted(edges)],
                 "transactions": [{**t, "date": t["date"].isoformat()} for t in tx]}
    dataset_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:16]
    sensitivity = priority_sensitivity(rows, priority_features)
    return {"nodes": rows, "edges": list(edges.values()), "clusters": cluster_rows,
            "transactions": canonical["transactions"], "meta": {
                "demo": demo, "dataset_hash": dataset_hash, "n_nodes": len(nodes), "n_edges": len(edges),
                "n_transactions": len(tx), "n_seed": len(seeds), "n_clusters": len(communities),
                "n_components": nx.number_weakly_connected_components(g),
                "n_active_components": nx.number_weakly_connected_components(g.subgraph([v for v in g if g.degree(v) > 0])) if edges else 0,
                "n_isolated": sum(g.degree(v) == 0 for v in g),
                "n_boundary": sum(r["truncated_by_depth"] for r in rows),
                "n_multiseed_clusters": sum(c["n_seed"] > 1 for c in cluster_rows),
                "turnover": round(sum(e["sum_kzt"] for e in edges.values()), 2),
                "period": [tx[0]["date"].date().isoformat(), tx[-1]["date"].date().isoformat()] if tx else [],
                "elapsed_seconds": round(time.perf_counter() - start, 3),
                "priority_sensitivity": sensitivity,
                "warnings": warnings, "limitations": LIMITS, "roles": ROLES, "colors": COLORS,
            }}


def write_csv(path, rows, fields):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def export(result, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    write_csv(folder / "nodes_roles.csv", result["nodes"], NODE_FIELDS)
    write_csv(folder / "clusters.csv", result["clusters"], CLUSTER_FIELDS)
    # Include all nodes; this always meets >=20 when the dataset has >=20 nodes.
    write_csv(folder / "top_nodes.csv", [dict(r, rank=i + 1) for i, r in enumerate(result["nodes"])], TOP_FIELDS)
    (folder / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    (folder / "quality.json").write_text(json.dumps(result["meta"], ensure_ascii=False, indent=2), encoding="utf-8")
