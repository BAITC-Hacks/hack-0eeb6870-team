"""Local-only HTTP interface. Static assets are bundled; no CDN or telemetry."""
from __future__ import annotations
import csv
import io
import json
import mimetypes
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pyarrow.parquet as pq

from .analysis import analyze, export, NODE_FIELDS, CLUSTER_FIELDS, TOP_FIELDS
from .assistant import Assistant, wire

STATIC = Path(__file__).parent / "static"


class App:
    def __init__(self, result, output):
        self.current = (result, Assistant(result))
        self.output = Path(output)
        self.lock = threading.Lock()

    @property
    def result(self):
        return self.current[0]

    @property
    def assistant(self):
        return self.current[1]

    def replace(self, payload):
        if not self.lock.acquire(blocking=False):
            raise ValueError("Другой пересчёт уже выполняется")
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as z:
                tables = {}
                for name in ("nodes", "edges", "transactions"):
                    candidates = [i for i in z.infolist() if Path(i.filename).name == f"{name}.parquet"]
                    if len(candidates) != 1:
                        raise ValueError(f"В ZIP должен быть ровно один {name}.parquet")
                    info = candidates[0]
                    if info.file_size > 50_000_000:
                        raise ValueError("Размер одного Parquet не должен превышать 50 MB")
                    # Read in memory; never extract user-controlled archive paths.
                    table = pq.read_table(io.BytesIO(z.read(info)))
                    if table.num_rows > 250_000:
                        raise ValueError("Прототип принимает до 250 000 строк в каждой таблице")
                    tables[name] = table.to_pylist()
                demo = any(Path(i.filename).name == "DEMO.txt" for i in z.infolist())
            if len(tables["nodes"]) > 10_000:
                raise ValueError("Лимит интерактивного прототипа: 10 000 узлов")
            result = analyze(tables, demo=demo)
            # Separate versions prevent partial CSV downloads during a rebuild.
            destination = self.output / ("dataset-" + result["meta"]["dataset_hash"])
            export(result, destination)
            assistant = Assistant(result)
            with self.assistant.lock:
                assistant.calls = self.assistant.calls  # uploads must not reset API budget
                self.output = destination
                self.current = (result, assistant)
            return result["meta"]
        finally:
            self.lock.release()


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # no questions, tokens or client ids in access logs

        def send(self, status, body, content_type="application/json; charset=utf-8", filename=None):
            if not isinstance(body, bytes):
                body = json.dumps(wire(body), ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def local_request(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            return host in allowed and (not origin or origin in {"http://" + h for h in allowed})

        def do_GET(self):
            if not self.local_request():
                return self.send(403, {"error": "Разрешены только локальные запросы"})
            url = urlsplit(self.path)
            result, assistant = app.current
            try:
                if url.path == "/api/data":
                    return self.send(200, {k: v for k, v in result.items() if k != "transactions"})
                if url.path == "/api/status":
                    return self.send(200, {"configured": assistant.configured, "model": assistant.model, "provider": assistant.provider,
                                           "calls": assistant.calls, "max_calls": assistant.max_calls})
                if url.path == "/api/node":
                    gid = parse_qs(url.query).get("gid", [None])[0]
                    return self.send(200, assistant.tools.node_details(gid))
                if url.path.startswith("/download/"):
                    name = url.path.rsplit("/", 1)[1]
                    exports = {
                        "nodes_roles.csv": (result["nodes"], NODE_FIELDS),
                        "clusters.csv": (result["clusters"], CLUSTER_FIELDS),
                        "top_nodes.csv": ([dict(r, rank=i + 1) for i, r in enumerate(result["nodes"])], TOP_FIELDS),
                    }
                    if name not in exports:
                        return self.send(404, {"error": "Файл не найден"})
                    rows, fields = exports[name]
                    stream = io.StringIO(newline="")
                    writer = csv.DictWriter(stream, fields, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(rows)
                    return self.send(200, stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", name)
                assets = {"/": "index.html", "/app.js": "app.js", "/icons.js": "icons.js", "/style.css": "style.css"}
                if url.path in assets:
                    file = STATIC / assets[url.path]
                    kind = {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}[file.suffix]
                    return self.send(200, file.read_bytes(), kind + "; charset=utf-8")
                return self.send(404, {"error": "Не найдено"})
            except (ValueError, KeyError) as e:
                self.send(400, {"error": str(e)})

        def do_POST(self):
            if not self.local_request():
                return self.send(403, {"error": "Разрешены только локальные запросы"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                maximum = 50_000_000 if self.path == "/api/upload" else 8_000
                if not 0 < size <= maximum:
                    return self.send(413, {"error": "Недопустимый размер запроса"})
                body = self.rfile.read(size)
                if self.path == "/api/upload":
                    return self.send(200, app.replace(body))
                if self.path == "/api/chat":
                    data = json.loads(body)
                    if not isinstance(data, dict) or not isinstance(data.get("use_ai", False), bool):
                        raise ValueError("Некорректный запрос")
                    return self.send(200, app.assistant.answer(data.get("question"), data.get("selected"), data.get("use_ai", False)))
                return self.send(404, {"error": "Не найдено"})
            except (ValueError, TypeError, KeyError, zipfile.BadZipFile) as e:
                return self.send(400, {"error": str(e)})
            except Exception:
                return self.send(500, {"error": "Не удалось обработать файл или запрос. Проверьте схему и целостность Parquet."})

    return Handler


def serve(result, output, port=8765):
    app = App(result, output)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(app))
    print(f"MoneyGraph: http://127.0.0.1:{port}", flush=True)
    print("Ctrl+C — остановить. API-ключ читается только из окружения или локального .env.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
