"""個別要望（休み要望・シフト要望）を加えたモデルの検証（lin=1, 日数31, 72時間ルールあり）。
 base : 基本の休み要望（希望休。各人2日）のみ    req : 休み要望・シフト要望を全セルの rate だけ追加（基本の要望は残す）
使い方: python bench_requests.py [--runs 3] [--rate 0.10] [--ratio 0.3] [--sizes 30 60] [--seed 0]"""
import argparse, statistics as st
from nurse import Eps, solve, synthetic_for, with_requests
import verify, evaluate

ap = argparse.ArgumentParser()
ap.add_argument("--runs", type=int, default=3)
ap.add_argument("--time-limit", type=float, default=120)
ap.add_argument("--rate", type=float, default=0.10)
ap.add_argument("--ratio", type=float, default=0.3, help="要望ごとの許容違反割合 ρ")
ap.add_argument("--sizes", type=int, nargs="+", default=[30, 60])
ap.add_argument("--days", type=int, default=31)
ap.add_argument("--seed", type=int, default=0, help="インスタンスと要望の乱数seed")
a = ap.parse_args()

print(f"{'N':>3} {'variant':>7} {'要望(休/シフト)':>14} {'成功':>5} {'verify':>6} | {'solve中央値':>10} {'min':>7} {'max':>7} | {'制約':>6} | {'休み充足':>8} {'シフト充足':>9}", flush=True)
for N in a.sizes:
    I0 = synthetic_for(N, a.days, a.seed)
    for name, I in (("base", I0), ("req", with_requests(I0, a.rate, seed=a.seed))):
        eps = Eps(req_viol_ratio=a.ratio)
        nq = f"{sum(map(len, I.req_off.values()))}/{sum(map(len, I.req_shift.values()))}"
        sv, okc, vok, rows = [], 0, 0, []
        for _ in range(a.runs):
            res = solve(I, eps, time_limit=a.time_limit)
            sv.append(res.times["solve"])
            if res.ok:
                okc += 1
                A = verify.extract(res.model)
                vok += not verify.check(I, eps, A)
                rows.append(evaluate.request_stats(I, A))
        f = lambda k: (st.median(r[k]["rate"] for r in rows) if rows and rows[0][k]["total"] else float("nan"))
        print(f"{N:>3} {name:>7} {nq:>14} {okc:>2}/{a.runs:<2} {vok:>3}/{okc:<2} | {st.median(sv):>9.2f}s {min(sv):>6.2f}s {max(sv):>6.2f}s "
              f"| {sum(res.counts.values()):>6} | {f('休み要望'):>8.1%} {f('シフト要望'):>9.1%}", flush=True)
