"""linearization_level（0/1/2）の比較。各条件を複数回求解し、中央値・最小・最大を表示する。
使い方: python bench_lin.py [--runs 5] [--seeds 0 1 2] [--time-limit 60]"""
import argparse, statistics as st
from nurse import Eps, solve, synthetic
import verify

ap = argparse.ArgumentParser()
ap.add_argument("--runs", type=int, default=5)
ap.add_argument("--seeds", type=int, nargs="+", default=[0])
ap.add_argument("--time-limit", type=float, default=60)
a = ap.parse_args()

print(f"{'seed':>4} {'lin':>3} {'成功':>5} {'verify':>6} | {'solve中央値':>10} {'min':>6} {'max':>6} | {'transform中央値':>13}")
for seed in a.seeds:
    I, eps = synthetic(seed=seed), Eps()
    for lin in (0, 1, 2):
        sv, tr, okc, vok = [], [], 0, 0
        for _ in range(a.runs):
            res = solve(I, eps, time_limit=a.time_limit, lin_level=lin)
            sv.append(res.times["solve"]); tr.append(res.times["transform"])
            if res.ok:
                okc += 1
                vok += not verify.check(I, eps, verify.extract(res.model))
        print(f"{seed:>4} {lin:>3} {okc:>2}/{a.runs:<2} {vok:>3}/{okc:<2} | {st.median(sv):>9.2f}s {min(sv):>5.2f}s {max(sv):>5.2f}s | {st.median(tr):>12.2f}s")
