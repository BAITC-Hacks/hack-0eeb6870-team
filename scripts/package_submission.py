"""Rebuild the three competition CSVs and their reproducibility manifest."""
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from moneygraph.analysis import analyze, export, load_tables


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--out', type=Path, default=Path('submission'))
    args = parser.parse_args()
    result = analyze(load_tables(args.data), demo=(args.data / 'DEMO.txt').is_file())
    args.out.mkdir(parents=True, exist_ok=True)
    checksums = {}
    with tempfile.TemporaryDirectory() as directory:
        export(result, directory)
        for name in ('nodes_roles.csv', 'clusters.csv', 'top_nodes.csv'):
            source = Path(directory) / name
            shutil.copyfile(source, args.out / name)
            checksums[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    meta = result['meta']
    manifest = {key: meta[key] for key in ('dataset_hash', 'demo', 'n_nodes', 'n_edges',
                'n_transactions', 'n_clusters', 'n_boundary', 'limitations', 'priority_sensitivity')}
    manifest['sha256'] = checksums
    (args.out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Submission: {args.out.resolve()} ({meta['n_nodes']} nodes)")


if __name__ == '__main__':
    main()
