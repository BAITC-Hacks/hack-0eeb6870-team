import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from moneygraph.analysis import analyze
from moneygraph.demo import make_demo
from moneygraph.server import App


class UploadRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tables = make_demo(self.root / 'data')
        self.result = analyze(self.tables, demo=True)
        self.app = App(self.result, self.root / 'results')
        self.payload = self.zip_tables()

    def tearDown(self):
        self.temp.cleanup()

    def zip_tables(self, override=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name in ('nodes', 'edges', 'transactions'):
                raw = (self.root / 'data' / (name + '.parquet')).read_bytes()
                if override and name in override:
                    buffer = io.BytesIO()
                    pq.write_table(override[name], buffer)
                    raw = buffer.getvalue()
                archive.writestr('data/' + name + '.parquet', raw)
        return stream.getvalue()

    def test_repeated_uploads_reuse_root_not_nested_directories(self):
        for _ in range(4):
            self.app.replace(self.payload)
            self.assertEqual(self.app.output.parent, self.root / 'results')
        self.assertEqual(len(list((self.root / 'results').glob('dataset-*'))), 1)
        self.assertEqual(len(list((self.root / 'results').rglob('nodes_roles.csv'))), 1)

    def test_old_assistant_and_replacement_share_one_budget(self):
        with patch.dict(os.environ, {'NVIDIA_MAX_CALLS': '2'}):
            app = App(self.result, self.root / 'budget-output')
        old = app.assistant
        app.replace(self.payload)
        # An old HTTP handler may still hold this instance after replacement.
        self.assertIs(old.budget, app.assistant.budget)
        old.budget.consume()
        app.assistant.budget.consume()
        with self.assertRaises(RuntimeError):
            old.budget.consume()
        self.assertEqual(app.assistant.calls, 2)

    def test_metadata_limit_is_checked_before_decoding_table(self):
        nodes = pa.table({'gid': range(10001), 'depth': [0] * 10001, 'is_seed': [True] * 10001})
        payload = self.zip_tables({'nodes': nodes})
        with patch.object(pq.ParquetFile, 'read', side_effect=AssertionError('must not decode')):
            with self.assertRaisesRegex(ValueError, '10000'):
                self.app.replace(payload)
        self.assertIs(self.app.result, self.result)

    def test_extra_columns_are_not_decoded(self):
        table = pq.read_table(self.root / 'data/nodes.parquet')
        table = table.append_column('irrelevant_blob', pa.array(['extra'] * len(table)))
        self.app.replace(self.zip_tables({'nodes': table}))
        self.assertNotIn('irrelevant_blob', self.app.result['nodes'][0])

    def test_uncompressed_budget_rejects_upload_and_keeps_old_result(self):
        with patch('moneygraph.server.MAX_DECODED_BYTES', 1):
            with self.assertRaisesRegex(ValueError, '100 MB'):
                self.app.replace(self.payload)
        self.assertIs(self.app.result, self.result)


if __name__ == '__main__':
    unittest.main()
