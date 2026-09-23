import copy
import csv
import io
import json
import os
import random
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from moneygraph.analysis import analyze, choose_role, export, load_tables, normalize, temporal_features
from moneygraph.assistant import Assistant, GraphTools, wire
from moneygraph.demo import make_demo
from moneygraph.server import App, handler_for


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.tables = make_demo(cls.temp.name)
        cls.result = analyze(cls.tables, demo=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_parquet_roundtrip_and_all_exports(self):
        result = analyze(load_tables(self.temp.name))
        with tempfile.TemporaryDirectory() as dest:
            export(result, dest)
            for name, expected in [('nodes_roles',145),('top_nodes',145),('clusters',len(result['clusters']))]:
                with (Path(dest)/f'{name}.csv').open(encoding='utf-8-sig',newline='') as f:
                    rows=list(csv.DictReader(f))
                self.assertEqual(len(rows),expected)
                required = ['gid','role','role_score','cluster_id','priority_score','evidence'] if name=='nodes_roles' else list(rows[0])
                self.assertTrue(all(all(r[k] != '' for k in required) for r in rows))
        self.assertTrue(all(0 <= n['role_score'] <= 1 and 0 <= n['priority_score'] <= 1 for n in result['nodes']))
        self.assertTrue(all(len(n['evidence']) <= 200 for n in result['nodes']))

    def test_boundary_never_becomes_terminal(self):
        boundary=[n for n in self.result['nodes'] if n['depth']==4]
        self.assertEqual(len(boundary),32)
        self.assertTrue(all(n['role']=='peripheral' and n['role_score']==0 and n['truncated_by_depth'] for n in boundary))
        self.assertEqual(len(GraphTools(self.result).seed_path(1070)),5)

    def test_isolated_seed_preserved(self):
        isolated=[n for n in self.result['nodes'] if n['gid'] in (1099,1199,1299,1399)]
        self.assertEqual(len(isolated),4)
        self.assertTrue(all(n['priority_score']==0 and n['role']=='peripheral' for n in isolated))

    def test_precedence_and_roles(self):
        base=dict(in_degree=1,out_degree=1,pass_ratio=1,depth=1,is_seed=False,
                  neighbor_clusters=1,seed_reach=1,fast_ratio=.9)
        examples=[({},'transit'),({'in_degree':10,'pass_ratio':.03},'consolidator'),
                  ({'out_degree':12},'distributor'),({'out_degree':0,'pass_ratio':0},'terminal'),
                  ({'out_degree':0,'depth':4,'in_degree':12,'pass_ratio':0},'peripheral'),
                  ({'out_degree':3,'in_degree':3,'neighbor_clusters':3,'seed_reach':4},'coordinator'),
                  ({'is_seed':True},'peripheral')]
        for change,role in examples:
            with self.subTest(role=role):self.assertEqual(choose_role(dict(base,**change))[0],role)

    def test_fifo_not_double_spent_or_reverse_time(self):
        day=datetime(2026,7,1,tzinfo=timezone.utc)
        tx=[dict(src=1,dst=2,date=day,sum_kzt=100),
            dict(src=2,dst=3,date=day,sum_kzt=100),
            dict(src=2,dst=3,date=day+timedelta(days=1),sum_kzt=60),
            dict(src=2,dst=4,date=day+timedelta(days=2),sum_kzt=60)]
        self.assertEqual(temporal_features(tx)[2],100)
        tx[-1]['date']=day+timedelta(days=3)
        self.assertEqual(temporal_features(tx)[2],60)

    def test_inconsistent_aggregates_fail(self):
        bad=copy.deepcopy(self.tables);bad['edges'][0]['sum_kzt']+=100
        with self.assertRaisesRegex(ValueError,'не согласованы'):normalize(bad)

    def test_unknown_ids_duplicate_ids_and_nan_fail(self):
        bad=copy.deepcopy(self.tables);bad['nodes'].append(bad['nodes'][0])
        with self.assertRaisesRegex(ValueError,'Дублируется'):normalize(bad)
        bad=copy.deepcopy(self.tables);bad['transactions'][0]['src']=999999
        with self.assertRaisesRegex(ValueError,'отсутствующий'):normalize(bad)
        bad=copy.deepcopy(self.tables);bad['edges'][0]['sum_kzt']=float('nan')
        with self.assertRaises(ValueError):normalize(bad)

    def test_deterministic_after_reordering(self):
        shuffled=copy.deepcopy(self.tables)
        for rows in shuffled.values():random.Random(11).shuffle(rows)
        result=analyze(shuffled, demo=True)
        self.assertEqual(self.result['nodes'],result['nodes'])
        self.assertEqual(self.result['clusters'],result['clusters'])
        self.assertEqual(self.result['meta']['dataset_hash'],result['meta']['dataset_hash'])

    def test_common_recipients_and_direction(self):
        tools=GraphTools(self.result)
        common=tools.common_recipients(['1000','1001'])
        self.assertEqual({r['gid'] for r in common['recipients']},{1020,1021})
        self.assertEqual(tools.find_path('1000','1030')['path'],[1000,1021,1030])
        self.assertEqual(tools.find_path('1030','1000')['path'],[])
        with self.assertRaises(ValueError):tools.common_recipients(['1000','999999'])

    def test_int64_wire_is_exact(self):
        huge=2**63-1
        self.assertEqual(wire({'gid':huge,'src':huge,'path':[huge]})['path'],[str(huge)])

    def test_empty_edges_keeps_nodes(self):
        result=analyze({'nodes':[dict(gid=1,depth=0,is_seed=True)],'edges':[],'transactions':[]})
        self.assertEqual(result['nodes'][0]['priority_score'],0)

    def test_offline_key_absent_never_calls_transport(self):
        def forbidden(body):self.fail('API must not be called')
        with patch.dict(os.environ, {'NVIDIA_API_KEY':''}):
            assistant=Assistant(self.result, forbidden)
            r=assistant.answer('Общие получатели 1000 1001',use_ai=True)
            self.assertEqual(r['mode'],'local');self.assertIn(1020,r['references'])
            self.assertEqual(assistant.calls,0)

    def test_nvidia_tool_roundtrip_cache_and_request_shape(self):
        calls=[]
        def fake(body):
            calls.append(body)
            if len(calls)==1:
                return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[
                    {'id':'call_1','type':'function','function':{'name':'node_details','arguments':'{"gid":"1020"}'}}]}}]}
            return {'choices':[{'message':{'content':'[gid:1020] — признаки консолидации; гипотеза для проверки.'}}]}
        with patch.dict(os.environ, {'NVIDIA_API_KEY':'unit-test-only'}):
            assistant=Assistant(self.result,fake)
            result=assistant.answer('Справка по 1020',use_ai=True)
            self.assertEqual(result['mode'],'nvidia');self.assertEqual(assistant.calls,2)
            self.assertEqual(calls[0]['tool_choice'],'required')
            self.assertFalse(calls[0]['chat_template_kwargs']['enable_thinking'])
            self.assertEqual(calls[1]['messages'][-1]['role'],'tool')
            self.assertTrue(assistant.answer('Справка по 1020',use_ai=True)['cached'])
            self.assertEqual(assistant.calls,2)

    def test_api_failure_falls_back_without_secret(self):
        def fail(body):raise RuntimeError('NVIDIA API недоступен')
        with patch.dict(os.environ, {'NVIDIA_API_KEY':'do-not-leak'}):
            assistant=Assistant(self.result,fail)
            result=assistant.answer('Справка по 1020',use_ai=True)
            self.assertEqual(result['mode'],'local');self.assertNotIn('do-not-leak',json.dumps(result))

    def test_mixed_language_response_falls_back(self):
        responses=iter([
            {'choices':[{'message':{'tool_calls':[{'id':'c','type':'function','function':{'name':'node_details','arguments':'{"gid":"1020"}'}}]}}]},
            {'choices':[{'message':{'content':'[gid:1020] русский текст 混合语言'}}]},
        ])
        with patch.dict(os.environ,{'NVIDIA_API_KEY':'test'}):
            result=Assistant(self.result,lambda body:next(responses)).answer('Справка по 1020',use_ai=True)
            self.assertEqual(result['mode'],'local')
            self.assertIn('смешала языки',result['warning'])

    def test_unknown_llm_tool_rejected(self):
        def fake(body):return {'choices':[{'message':{'tool_calls':[{'id':'c','function':{'name':'execute_shell','arguments':'{}'}}]}}]}
        with patch.dict(os.environ, {'NVIDIA_API_KEY':'test'}):
            result=Assistant(self.result,fake).answer('Справка по 1020',use_ai=True)
            self.assertEqual(result['mode'],'local')

    def test_budget_enforced(self):
        with patch.dict(os.environ, {'NVIDIA_API_KEY':'test','NVIDIA_MAX_CALLS':'0'}):
            result=Assistant(self.result).answer('Справка по 1020',use_ai=True)
            self.assertEqual(result['mode'],'local');self.assertIn('лимит',result['warning'])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.result=analyze(make_demo(self.temp.name),demo=True)
        self.app=App(self.result,Path(self.temp.name)/'output')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.app))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.temp.cleanup()

    def test_http_data_download_and_chat(self):
        with urllib.request.urlopen(self.url+'/api/data') as r:body=json.load(r)
        self.assertIsInstance(body['nodes'][0]['gid'],str)
        with urllib.request.urlopen(self.url+'/download/nodes_roles.csv') as r:
            rows=list(csv.DictReader(io.StringIO(r.read().decode('utf-8-sig'))))
        self.assertEqual(len(rows),145)
        request=urllib.request.Request(self.url+'/api/chat',data=json.dumps({'question':'Справка по 1020'}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request) as r:reply=json.load(r)
        self.assertEqual(reply['mode'],'local')

    def test_upload_recalculates_and_retains_old_on_error(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            for p in Path(self.temp.name).glob('*.parquet'):z.write(p,'data/'+p.name)
        request=urllib.request.Request(self.url+'/api/upload',data=stream.getvalue())
        with urllib.request.urlopen(request) as r:self.assertEqual(json.load(r)['n_nodes'],145)
        request=urllib.request.Request(self.url+'/api/upload',data=b'broken zip')
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
        self.assertEqual(error.exception.code,400)
        self.assertEqual(self.app.result['meta']['n_nodes'],145)

    def test_cross_origin_post_rejected(self):
        request=urllib.request.Request(self.url+'/api/chat',data=b'{}',headers={'Origin':'https://other.example'})
        with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(request)
        self.assertEqual(e.exception.code,403)


if __name__ == '__main__':unittest.main()
