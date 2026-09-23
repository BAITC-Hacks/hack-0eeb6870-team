# Regression: ISSUE-002 — platform line endings invalidated submission checksums.
# Found by /qa on 2026-09-23.
# Report: .gstack/qa-reports/qa-report-localhost-2026-09-23.md
import codecs
import csv
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from moneygraph.analysis import analyze, export, write_csv
from moneygraph.demo import make_demo
from moneygraph.server import App, handler_for


ROOT = Path(__file__).resolve().parents[1]
CSV_NAMES = ("nodes_roles.csv", "clusters.csv", "top_nodes.csv")


class ExportReproducibilityTests(unittest.TestCase):
    def test_checked_in_submission_hashes_match_exact_csv_bytes(self):
        folder = ROOT / "submission"
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["sha256"]), set(CSV_NAMES))
        for name in CSV_NAMES:
            with self.subTest(file=name):
                payload = (folder / name).read_bytes()
                self.assertNotIn(b"\r\n", payload)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), manifest["sha256"][name])

    def test_csv_writer_uses_lf_and_preserves_bom_quoting_and_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "test.csv"
            rows = [{"gid": "100000003701161100", "evidence": 'Гипотеза, не "вывод"'}]
            write_csv(target, rows, ["gid", "evidence"])
            payload = target.read_bytes()
        self.assertTrue(payload.startswith(codecs.BOM_UTF8))
        self.assertNotIn(b"\r", payload)
        self.assertTrue(payload.endswith(b"\n"))
        parsed = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
        self.assertEqual(parsed, rows)

    def test_packaging_is_reproducible_and_hashes_the_exported_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tables = make_demo(root / "data")
            export(analyze(tables, demo=True), root / "export")
            command = [sys.executable, str(ROOT / "scripts/package_submission.py"),
                       "--data", str(root / "data"), "--out", str(root / "package")]
            subprocess.run(command, check=True, capture_output=True, text=True)
            first = {name: (root / "package" / name).read_bytes()
                     for name in (*CSV_NAMES, "manifest.json")}
            manifest = json.loads(first["manifest.json"])
            for name in CSV_NAMES:
                with self.subTest(file=name):
                    self.assertNotIn(b"\r\n", first[name])
                    self.assertEqual(first[name], (root / "export" / name).read_bytes())
                    self.assertEqual(hashlib.sha256(first[name]).hexdigest(), manifest["sha256"][name])
            subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertEqual(first, {name: (root / "package" / name).read_bytes() for name in first})

    def test_http_downloads_match_disk_exports_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = analyze(make_demo(root / "data"), demo=True)
            export(result, root / "export")
            app = App(result, root / "export")
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(app))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for name in CSV_NAMES:
                    with self.subTest(file=name):
                        url = f"http://127.0.0.1:{server.server_port}/download/{name}"
                        with urllib.request.urlopen(url, timeout=5) as response:
                            self.assertEqual(response.read(), (root / "export" / name).read_bytes())
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()


if __name__ == "__main__":
    unittest.main()
