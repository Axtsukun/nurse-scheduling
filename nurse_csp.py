"""
ナーススケジューリング CSP エンジンの入口。

実装は `nurse/` パッケージにある（構成は nurse/__init__.py と CLAUDE.md を参照）。このモジュールは
`import nurse_csp as m` で全ての公開 API を使えるようにする互換の窓口と、合成インスタンス1問の求解 CLI を持つ。

使い方:  python nurse_csp.py [--lin 0|1|2] [--time-limit 秒]     （評価は evaluate.py）
"""
from nurse import *            # noqa: F401,F403  公開 API を再エクスポート
from nurse import (Eps, synthetic, precheck, solve, diagnose, roster)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="合成インスタンス1問を求解する（評価は evaluate.py）")
    ap.add_argument("--lin", type=int, default=1, help="linearization_level（0/1/2）")
    ap.add_argument("--time-limit", type=float, default=30)
    args = ap.parse_args()
    I, eps = synthetic(), Eps()
    issues = precheck(I, eps)
    if issues:
        print("事前検査NG:", *issues[:10], sep="\n  ")
    res = solve(I, eps, time_limit=args.time_limit, lin_level=args.lin)
    print(f"lin={args.lin} ok={res.ok} {res.status.exitstatus.name} "
          + " ".join(f"{k}={v:.2f}s" for k, v in res.times.items()))
    print("制約本数:", res.counts)
    if res.ok:
        print(*roster(res.model)[:6], sep="\n")
    else:
        print(diagnose(I, eps, time_limit=args.time_limit).explain())


if __name__ == "__main__":
    main()
