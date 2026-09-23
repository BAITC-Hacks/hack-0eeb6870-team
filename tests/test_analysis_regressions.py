"""Adversarial cases from the audit, plus checks for evidence consumers."""
from collections import defaultdict
import copy
import unittest

from moneygraph.analysis import (
    analyze, integer, normalize, priority_sensitivity, role_decision, PRIORITY_WEIGHTS,
)


def fixture(nodes, transactions):
    node_rows = [dict(gid=gid, depth=depth, is_seed=depth == 0) for gid, depth in nodes]
    depth_by_id = dict(nodes)
    totals = defaultdict(lambda: [0., 0])
    tx_rows = []
    for source, target, value, date in transactions:
        totals[source, target][0] += value
        totals[source, target][1] += 1
        tx_rows.append(dict(src=source, dst=target, sum_kzt=value, date=date))
    return {"nodes": node_rows, "transactions": tx_rows,
            "edges": [dict(src=a, dst=b, sum_kzt=value, n_tx=count, depth=depth_by_id[a] + 1)
                      for (a, b), (value, count) in totals.items()]}


class InputIntegrityTests(unittest.TestCase):
    def test_float_identifiers_rejected_before_precision_loss_is_accepted(self):
        for value in (1.0, float(100000003701161101), float("nan"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                integer(value, "gid")
        self.assertEqual(integer("100000003701161101", "gid"), 100000003701161101)
        self.assertEqual(integer(2**63-1, "gid"), 2**63-1)

    def test_float_rejected_in_each_identifier_column(self):
        tables = fixture([(1, 0), (2, 1)], [(1, 2, 10000., "2026-07-01")])
        for table, key in (("nodes", "gid"), ("edges", "src"), ("edges", "dst"),
                           ("transactions", "src"), ("transactions", "dst")):
            bad = copy.deepcopy(tables)
            bad[table][0][key] = float(bad[table][0][key])
            with self.subTest(table=table, key=key), self.assertRaises(ValueError):
                normalize(bad)

    def test_inconsistent_node_depth_cannot_create_false_boundary(self):
        tables = fixture([(1, 0), (2, 4)], [(1, 2, 10000., "2026-07-01")])
        with self.assertRaisesRegex(ValueError, "минимальным коленом"):
            normalize(tables)

    def test_edge_discovery_depth_and_reachability_checked(self):
        tables = fixture([(1, 0), (2, 1)], [(1, 2, 10000., "2026-07-01")])
        tables["edges"][0]["depth"] = 4
        with self.assertRaisesRegex(ValueError, "колено обнаружения 1"):
            normalize(tables)
        unreachable = fixture([(1, 0), (2, 1)], [])
        with self.assertRaisesRegex(ValueError, "недостижим от seed"):
            normalize(unreachable)

    def test_directed_shortest_distance_from_all_seeds_allows_return_edges(self):
        tables = fixture([(1, 0), (2, 1), (3, 2), (4, 0), (5, 0)], [
            (1, 2, 10000., "2026-07-01"), (2, 3, 10000., "2026-07-02"),
            (3, 2, 10000., "2026-07-03"), (4, 2, 10000., "2026-07-01"),
        ])
        nodes, _, _ = normalize(tables)
        self.assertEqual(len(nodes), 5)  # Includes isolated seed 5.


class EvidenceTests(unittest.TestCase):
    def test_canonical_rule_drives_primary_and_secondary_roles(self):
        metrics = dict(in_degree=4, out_degree=0, pass_ratio=0., depth=1, is_seed=False,
                       neighbor_clusters=1, seed_reach=4, fast_ratio=0.)
        role, score, rule, secondary = role_decision(metrics)
        self.assertEqual(role, "consolidator")
        self.assertEqual(rule["rule_id"], role)
        self.assertEqual(rule["precedence"], 4)
        self.assertTrue(all(c["passed"] for c in rule["conditions"]))
        self.assertEqual(secondary, ["terminal"])
        sender_condition = next(c for c in rule["conditions"] if c["metric"] == "in_degree")
        self.assertEqual((sender_condition["actual"], sender_condition["operator"], sender_condition["threshold"]), (4, ">=", 4))
        role, _, rule, secondary = role_decision(dict(metrics, depth=4))
        self.assertEqual((role, rule["rule_id"], secondary), ("peripheral", "boundary", []))

    def test_reverse_chronology_has_explicit_absence_of_temporal_support(self):
        tables = fixture([(1, 0), (2, 1), (3, 2)], [
            (1, 2, 10000., "2026-07-31"), (2, 3, 10000., "2026-07-01"),
        ])
        result = analyze(tables)
        node = next(n for n in result["nodes"] if n["gid"] == 2)
        self.assertEqual(node["role"], "transit")  # Preserve the disclosed aggregate rule.
        self.assertEqual(node["temporal_support"]["status"], "not_observed")
        self.assertEqual(node["temporal_support"]["matched_kzt"], 0.)
        self.assertIn("не подтверждён", node["role_rule"]["limitation"])

    def test_cluster_hypotheses_include_purpose_and_reconciled_flows(self):
        tables = fixture([(1, 0), (2, 1), (3, 2), (4, 0)], [
            (1, 2, 30000., "2026-07-01"), (2, 3, 5000., "2026-07-02"),
        ])
        result = analyze(tables)
        membership = {n["gid"]: n["cluster_id"] for n in result["nodes"]}
        for cluster in result["clusters"]:
            cid = cluster["cluster_id"]
            incoming = sum(e["sum_kzt"] for e in tables["edges"] if membership[e["src"]] != cid and membership[e["dst"]] == cid)
            outgoing = sum(e["sum_kzt"] for e in tables["edges"] if membership[e["src"]] == cid and membership[e["dst"]] != cid)
            self.assertEqual(cluster["sum_kzt_incoming_external"], incoming)
            self.assertEqual(cluster["sum_kzt_outgoing_external"], outgoing)
            self.assertEqual(sum(cluster["role_counts"].values()), cluster["n_nodes"])
            self.assertIn("KZT", cluster["hypothesis"])
            self.assertNotIn("Гипотезы по ведущим узлам", cluster["hypothesis"])
        isolated = next(c for c in result["clusters"] if c["top_gids"] == "4")
        self.assertIn("Назначение не определено", isolated["hypothesis"])


class SensitivityTests(unittest.TestCase):
    def test_weight_sensitivity_measures_changes_in_top_membership(self):
        # The 20th place flips between a volume-led and a seed-led candidate.
        features = {gid: dict.fromkeys(PRIORITY_WEIGHTS, 1.) for gid in range(19)}
        features[19] = dict.fromkeys(PRIORITY_WEIGHTS, 0.)
        features[19]["volume"] = 1.
        features[20] = dict.fromkeys(PRIORITY_WEIGHTS, 0.)
        features[20]["seed_reach"] = 1.
        rows = [{"gid": gid} for gid in range(21)]
        result = priority_sensitivity(rows, features)
        self.assertEqual(result["top_k"], 20)
        self.assertEqual(len(result["scenarios"]), 10)
        self.assertEqual(result["min_overlap_ratio"], .95)
        self.assertTrue(any(s["entered_gids"] == [20] for s in result["scenarios"]))
        self.assertTrue(all(abs(sum(s["weights"].values()) - 1) < 1e-12 for s in result["scenarios"]))
        self.assertEqual(result, priority_sensitivity(rows, features))


if __name__ == "__main__":
    unittest.main()
