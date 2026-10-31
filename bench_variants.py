"""対称性の除去 / 72時間ルール除外 の効果比較（lin=1 固定、seed=0）。
 base : 既定   sym : 同種看護師の夜勤回数を非増加に整列（relaxed）   no72 : 72時間ルールを外す   both : 両方
72時間ルールを外す条件は Rules.off で指定する。verify.check は外した制約の検査を自動で飛ばす。
使い方: python bench_variants.py [--runs 3] [--time-limit 120] [--sym relaxed|exact]"""
import argparse, dataclasses as dc, statistics as st
from nurse import Eps, solve, synthetic_for
import verify

ap = argparse.ArgumentParser()
ap.add_argument("--runs", type=int, default=3)
ap.add_argument("--time-limit", type=float, default=120)
ap.add_argument("--sym", default="relaxed")
ap.add_argument("--sizes", type=int, nargs="+", default=[30, 60, 90])
ap.add_argument("--days", type=int, nargs="+", default=[28, 30, 31])
a = ap.parse_args()
VARIANTS = {"base": ("", False), "sym": (a.sym, False), "no72": ("", True), "both": (a.sym, True)}   # (対称性, 72h除外)

print(f"{'N':>3} {'T':>3} {'variant':>7} {'成功':>5} {'verify':>6} | {'solve中央値':>10} {'min':>7} {'max':>7} | {'制約':>6} {'72h平均':>8}", flush=True)
for N in a.sizes:
    for T in a.days:
        I0 = synthetic_for(N, T)
        for name, (sym, drop72) in VARIANTS.items():
            I = dc.replace(I0, rules=dc.replace(I0.rules, off=frozenset({"72h"}) if drop72 else frozenset()))
            eps = Eps(symmetry_break=sym)
            sv, okc, vok, avg = [], 0, 0, []
            for _ in range(a.runs):
                res = solve(I, eps, time_limit=a.time_limit)
                sv.append(res.times["solve"])
                if res.ok:
                    okc += 1
                    A = verify.extract(res.model)
                    vok += not verify.check(I, eps, A)
                    avg.append(verify.report(I, A)["avg_night_hours"])
            print(f"{N:>3} {T:>3} {name:>7} {okc:>2}/{a.runs:<2} {vok:>3}/{okc:<2} | {st.median(sv):>9.2f}s {min(sv):>6.2f}s {max(sv):>6.2f}s "
                  f"| {sum(res.counts.values()):>6} {st.median(avg) if avg else float('nan'):>7.1f}h", flush=True)
