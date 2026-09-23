"""Synthetic performance benchmark with exactly the case's table sizes."""
import json
import random
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from moneygraph.analysis import analyze, export, load_tables


def main():
    rng=random.Random(42)
    # 81 seeds; every other node reachable from one seed in <=4 steps.
    nodes=[dict(gid=i, depth=0 if i<81 else 1+(i-81)%4, is_seed=i<81) for i in range(2248)]
    by_depth={d:[n['gid'] for n in nodes if n['depth']==d] for d in range(5)}
    pairs=set()
    for n in nodes[81:]:pairs.add((rng.choice(by_depth[n['depth']-1]),n['gid']))
    while len(pairs)<3119:
        depth=rng.randrange(4)
        pairs.add((rng.choice(by_depth[depth]),rng.choice(by_depth[depth+1])))
    ordered=sorted(pairs)
    tx=[]
    for i in range(4840):
        a,b=ordered[i] if i<len(ordered) else rng.choice(ordered)
        tx.append(dict(src=a,dst=b,sum_kzt=float(rng.randrange(1,50)*5000),date=datetime(2026,7,1)+timedelta(days=rng.randrange(31))))
    sums=defaultdict(lambda:[0.,0])
    for t in tx:sums[t['src'],t['dst']][0]+=t['sum_kzt'];sums[t['src'],t['dst']][1]+=1
    edges=[dict(src=a,dst=b,sum_kzt=s,n_tx=n,depth=nodes[a]['depth']+1) for (a,b),(s,n) in sorted(sums.items())]
    with tempfile.TemporaryDirectory() as folder:
        folder=Path(folder)
        for name,rows in [('nodes',nodes),('edges',edges),('transactions',tx)]:pq.write_table(pa.Table.from_pylist(rows),folder/f'{name}.parquet')
        start=time.perf_counter()
        result=analyze(load_tables(folder))
        export(result,folder/'results')
        elapsed=time.perf_counter()-start
        print(json.dumps({'nodes':len(nodes),'edges':len(edges),'transactions':len(tx),
                          'parquet_to_exports_seconds':round(elapsed,3),'within_5_minutes':elapsed<=300},indent=2))
        if elapsed>300:raise SystemExit(1)


if __name__=='__main__':main()
