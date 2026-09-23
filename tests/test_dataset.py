"""Regression checks against the supplied HackAlem files; no hardcoded roles by gid."""
import csv
import io
import json
import re
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import networkx as nx
import numpy as np

from moneygraph.analysis import analyze, export, load_tables, weighted_pagerank, ROLES
from moneygraph.assistant import Assistant, GraphTools, wire

DATA = Path(__file__).resolve().parents[1] / 'data'


class PageRankTests(unittest.TestCase):
    def test_matches_stationary_linear_system_and_isolates(self):
        g=nx.DiGraph()
        g.add_weighted_edges_from([(1,2,3),(1,3,1),(2,1,2)])
        g.add_node(9)
        result=weighted_pagerank(g)
        transition=np.array([[0,.75,.25],[1,0,0],[1/3,1/3,1/3]])
        expected=np.linalg.solve(np.eye(3)-.85*transition.T,np.full(3,.15/3))
        np.testing.assert_allclose([result[v] for v in [1,2,3]],expected,rtol=1e-8)
        self.assertEqual(result[9],0)
        self.assertAlmostEqual(sum(result.values()),1)


@unittest.skipUnless((DATA/'nodes.parquet').is_file(), 'Реальный датасет не приложен')
class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables=load_tables(DATA)
        cls.result=analyze(cls.tables)

    def test_dataset_scope_and_reported_ambiguities(self):
        m=self.result['meta']
        self.assertEqual((m['n_nodes'],m['n_edges'],m['n_transactions'],m['n_seed']),(2248,3119,4840,81))
        self.assertEqual((m['n_components'],m['n_active_components'],m['n_isolated']),(35,16,19))
        self.assertEqual(m['n_boundary'],444)
        self.assertEqual(m['n_multiseed_clusters'],8)
        self.assertAlmostEqual(m['turnover'],365890012.01,places=2)
        self.assertEqual(Counter(n['depth'] for n in self.result['nodes']),{0:81,1:472,2:462,3:789,4:444})

    def test_official_roles_numeric_evidence_and_isolated_seeds(self):
        rows=self.result['nodes']
        self.assertEqual(set(n['role'] for n in rows),set(ROLES))
        self.assertTrue(all(re.search(r'\d',n['evidence']) and len(n['evidence'])<=200 for n in rows))
        cut=[n for n in rows if n['truncated_by_depth']]
        self.assertTrue(all(n['role']=='peripheral' and n['role_score']==0 for n in cut))
        self.assertEqual(sum(n['is_seed'] and n['in_deg']==n['out_deg']==0 for n in rows),19)

    def test_starter_metrics_match_raw_edges(self):
        inc, out, it, ot=Counter(),Counter(),Counter(),Counter()
        for e in self.tables['edges']:
            inc[e['dst']]+=e['sum_kzt'];out[e['src']]+=e['sum_kzt']
            it[e['dst']]+=e['n_tx'];ot[e['src']]+=e['n_tx']
        for n in self.result['nodes']:
            gid=n['gid']
            self.assertAlmostEqual(n['in_kzt'],inc[gid],places=2)
            self.assertAlmostEqual(n['out_kzt'],out[gid],places=2)
            self.assertEqual((n['in_tx'],n['out_tx']),(it[gid],ot[gid]))
        self.assertAlmostEqual(sum(n['pagerank'] for n in self.result['nodes']),1,places=8)

    def test_real_int64_survives_csv_and_http_encoding(self):
        raw_ids={n['gid'] for n in self.tables['nodes']}
        self.assertTrue(all(v>2**53 for v in raw_ids))
        encoded=json.loads(json.dumps(wire(self.result)))
        self.assertEqual({int(n['gid']) for n in encoded['nodes']},raw_ids)
        with tempfile.TemporaryDirectory() as d:
            export(self.result,d)
            with (Path(d)/'nodes_roles.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
            self.assertEqual({int(n['gid']) for n in rows},raw_ids)
            self.assertEqual(len(rows),2248)

    def test_top_rank_order_and_priority_decomposition(self):
        rows=self.result['nodes']
        self.assertEqual(rows,sorted(rows,key=lambda n:(-n['priority_score'],n['gid'])))
        for n in rows:self.assertAlmostEqual(n['priority_score'],sum(n['priority_factors'].values()),places=6)

    def test_real_assistant_lookup_and_common_recipients(self):
        candidate=next(n for n in self.result['nodes'] if n['role']=='consolidator')
        tools=GraphTools(self.result)
        parents=sorted({e['src'] for e in self.tables['edges'] if e['dst']==candidate['gid']})[:2]
        result=tools.common_recipients([str(v) for v in parents])
        self.assertIn(candidate['gid'],[r['gid'] for r in result['recipients']])
        report=Assistant(self.result).answer('Справка по '+str(candidate['gid']))
        self.assertIn(candidate['gid'],report['references'])
        self.assertEqual(report['mode'],'local')


if __name__=='__main__':unittest.main()
