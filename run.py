"""python run.py --demo | python run.py --data data [--no-serve]"""
import argparse
import os
from pathlib import Path

from moneygraph.analysis import analyze, export, load_tables
from moneygraph.demo import make_demo


def load_env(path):
    if path.is_file():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" in raw and not raw.lstrip().startswith("#"):
                key, value = raw.split("=", 1)
                if key.strip() in ("NVIDIA_API_KEY", "NVIDIA_MODEL", "NVIDIA_MAX_CALLS", "NVIDIA_BASE_URL"):
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    parser = argparse.ArgumentParser(description="Граф денег — локальный анализ транзакций")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("results"))
    parser.add_argument("--demo", action="store_true", help="Сгенерировать синтетический датасет в demo-data")
    parser.add_argument("--no-serve", action="store_true", help="Создать выгрузки и завершить")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    load_env(Path(__file__).parent / ".env")
    try:
        tables = make_demo(Path("demo-data")) if args.demo else load_tables(args.data)
        result = analyze(tables, demo=args.demo or (args.data / "DEMO.txt").exists())
        export(result, args.out)
        meta = result["meta"]
        print(f"{'DEMO | ' if meta['demo'] else ''}{meta['n_nodes']} узлов, {meta['n_edges']} рёбер, "
              f"{meta['n_clusters']} кластеров. Расчёт: {meta['elapsed_seconds']} с. Выгрузки: {args.out.resolve()}")
        if not args.no_serve:
            from moneygraph.server import serve
            serve(result, args.out, args.port)
    except (ValueError, OSError) as e:
        parser.exit(1, f"Ошибка: {e}\n")


if __name__ == "__main__":
    main()
