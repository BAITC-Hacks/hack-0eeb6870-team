import os
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from moneygraph.assistant import Assistant


class BrevEndpointTests(unittest.TestCase):
    def test_local_tunnel_without_api_key(self):
        captured=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                captured.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length']))),self.headers.get('Authorization')))
                payload=json.dumps({'choices':[{'message':{'content':'test'}}]}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch.dict(os.environ,{'NVIDIA_BASE_URL':f'http://127.0.0.1:{server.server_port}/v1','NVIDIA_API_KEY':'','NVIDIA_MODEL':'moneygraph-qwen'}):
                assistant=Assistant({'nodes':[],'edges':[]})
                self.assertTrue(assistant.configured)
                self.assertEqual(assistant._completion([{'role':'user','content':'test'}],False)['content'],'test')
                self.assertEqual(captured[0][0],'/v1/chat/completions')
                self.assertEqual(captured[0][1]['model'],'moneygraph-qwen')
                self.assertIsNone(captured[0][2])
        finally:server.shutdown();server.server_close()

    def test_remote_cleartext_disallowed(self):
        with patch.dict(os.environ,{'NVIDIA_BASE_URL':'http://example.com/v1'}):
            with self.assertRaises(ValueError):Assistant({'nodes':[],'edges':[]})

    def test_credentials_in_url_disallowed(self):
        with patch.dict(os.environ,{'NVIDIA_BASE_URL':'https://user:password@example.com/v1'}):
            with self.assertRaises(ValueError):Assistant({'nodes':[],'edges':[]})
