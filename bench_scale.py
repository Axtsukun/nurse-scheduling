"""規模（看護師数×日数）× linearization_level の求解時間比較。
看護師数 N = 常勤 + パート（常勤:パート = 26:4 の比）。需要・資格者数は N に比例。
使い方: python bench_scale.py [--runs 3] [--time-limit 120]"""
import argparse, statistics as st
from nurse import Eps, solve, synthetic_for
import verify

ap = argparse.ArgumentParser()
ap.add_argument("--runs", type=int, default=3)
ap.add_argument("--time-limit", type=float, default=120)
ap.add_argument("--sizes", type=int, nargs="+", default=[30, 60, 90])
ap.add_argument("--days", type=int, nargs="+", default=[28, 30, 31])
a = ap.parse_args()

print(f"{'N':>3} {'T':>3} {'lin':>3} {'成功':>5} {'verify':>6} | {'solve中央値':>10} {'min':>7} {'max':>7} | {'変数':>7} {'制約':>6} {'build':>6} {'transf':>6}", flush=True)
for N in a.sizes:
    for T in a.days:
        I, eps = synthetic_for(N, T), Eps()
        for lin in (0, 1, 2):
            sv, okc, vok = [], 0, 0
            for _ in range(a.runs):
                res = solve(I, eps, time_limit=a.time_limit, lin_level=lin)
                sv.append(res.times["solve"])
                if res.ok:
                    okc += 1
                    vok += not verify.check(I, eps, verify.extract(res.model))
            nv = sum(v.size for v in res.model.xv)
            print(f"{N:>3} {T:>3} {lin:>3} {okc:>2}/{a.runs:<2} {vok:>3}/{okc:<2} | {st.median(sv):>9.2f}s {min(sv):>6.2f}s {max(sv):>6.2f}s "
                  f"| {nv:>7} {sum(res.counts.values()):>6} {res.times['build']:>5.1f}s {res.times['transform']:>5.1f}s", flush=True)
