"""Clearly labelled synthetic fixtures; no assumptions about real gid values."""
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import random

import pyarrow as pa
import pyarrow.parquet as pq


def make_demo(folder):
    rng = random.Random(42)
    nodes, transactions = {}, []

    def node(gid, depth, seed=False):
        nodes[gid] = {"gid": gid, "depth": depth, "is_seed": seed}

    def tx(a, b, value, day):
        transactions.append({"src": a, "dst": b, "sum_kzt": float(value),
                             "date": datetime(2026, 7, 1) + timedelta(days=day)})

    # Four networks with consolidators, fast transit, fans and censored sinks.
    for group in range(4):
        base = 1000 + group * 100
        for i in range(10):
            node(base + i, 0, True)
        for offset, depth in [(20, 1), (21, 1), (30, 2), (31, 2), (40, 3)]:
            node(base + offset, depth)
        for i in range(10):
            tx(base + i, base + 20, 70000 + rng.randrange(8) * 10000, 1)
            tx(base + i, base + 21, 30000, 5)
        tx(base + 20, base + 31, 10000, 3)
        tx(base + 21, base + 30, 285000, 6)
        for i in range(12):
            node(base + 50 + i, 3)
            tx(base + 30, base + 50 + i, 20000, 7)
        tx(base + 50, base + 31, 18000, 8)
        tx(base + 30, base + 40, 18000, 8)
        for i in range(8):
            node(base + 70 + i, 4)
            tx(base + 40, base + 70 + i, 5000, 9)
        node(base + 99, 0, True)  # isolated seed
    node(9000, 2)
    for group in range(4):
        base = 1000 + group * 100
        tx(base + 21, 9000, 12000, 10)
        tx(9000, base + 30, 11000, 11)
    edges = defaultdict(lambda: {"sum_kzt": 0., "n_tx": 0})
    for t in transactions:
        e = edges[t["src"], t["dst"]]
        e["sum_kzt"] += t["sum_kzt"]
        e["n_tx"] += 1
    data = {"nodes": list(nodes.values()), "transactions": transactions,
            "edges": [dict(src=a, dst=b, depth=min(4,nodes[a]["depth"] + 1), **e) for (a, b), e in sorted(edges.items())]}
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name, rows in data.items():
        pq.write_table(pa.Table.from_pylist(rows), folder / f"{name}.parquet")
    (folder / "DEMO.txt").write_text("Синтетические демонстрационные данные. Не датасет организаторов.\n", encoding="utf-8")
    return data
