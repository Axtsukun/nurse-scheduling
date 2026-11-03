"""
ナーススケジューリング CSP エンジンの入口。

実装は `nurse/` パッケージにある（構成は nurse/__init__.py と CLAUDE.md を参照）。このモジュールは
`import nurse_csp as m` で全ての公開 API を使えるようにする互換の窓口と、合成インスタンス1問の求解 CLI を持つ。

使い方:  python nurse_csp.py `[--lin 0|1|2] [--time-limit 秒] [--mus-time-limit 秒] [--properties パス] [--log-config パス]`
         （評価は evaluate.py。時間制限の既定は nurse.properties、ログの設定の既定は nurse.logging.yaml）

終了コード（nurse/codes.py の ReturnCode）:
  0 正常完了（解あり） / 10 求解が時間切れ / 20 制約矛盾（原因を最小まで特定） / 21 制約矛盾（原因の絞り込みが時間切れ）
  22 制約矛盾（原因を特定できず） / 30 入力・設定の誤り / 40 ソルバー・実行環境が使えない / 50 内部エラー / 2 引数エラー
"""
from __future__ import annotations
import logging
import sys
from nurse import *            # noqa: F401,F403  公開 API を再エクスポート
from nurse import (Eps, synthetic, precheck, solve, diagnose, roster, load_settings, ReturnCode, NurseError, code_of, setup_logging)

log = logging.getLogger("nurse.cli")


def run(argv: list[str] | None = None) -> ReturnCode:
    """CLI の本体。リターンコードを返す（例外は呼び出し側 main が終了コードにする）

    Args:
        argv: コマンドライン引数（`--lin`・`--time-limit`・`--mus-time-limit`・`--properties`）。省略すると `sys.argv` を使う。

    Returns:
        リターンコード。解が見つかれば `OK`、解けなければ診断結果のコード（`Diagnosis.code`）。"""
    import argparse
    ap = argparse.ArgumentParser(description="合成インスタンス1問を求解する（評価は evaluate.py）")
    ap.add_argument("--lin", type=int, default=1, help="linearization_level（0/1/2）")
    ap.add_argument("--time-limit", type=float, default=None, help="求解の時間制限（秒）。省略すると nurse.properties の solve.time_limit")
    ap.add_argument("--mus-time-limit", type=float, default=None,
                    help="解けないとき、矛盾の原因を絞る MUS 1回あたりの秒数。省略すると nurse.properties の mus.time_limit")
    ap.add_argument("--log-config", default=None, help="ログ設定ファイル（YAML / JSON）のパス。省略すると環境変数 NURSE_LOGGING、リポジトリ直下の nurse.logging.yaml の順")
    ap.add_argument("--properties", default=None, help="設定ファイル（properties）のパス。省略すると環境変数 NURSE_PROPERTIES、リポジトリ直下の nurse.properties の順")
    args = ap.parse_args(argv)
    setup_logging(args.log_config)
    log.info("起動: lin=%d time_limit=%s mus_time_limit=%s properties=%s", args.lin, args.time_limit, args.mus_time_limit, args.properties)
    cfg = load_settings(args.properties)
    time_limit = cfg.solve_time_limit if args.time_limit is None else args.time_limit
    mus_time_limit = cfg.mus_time_limit if args.mus_time_limit is None else args.mus_time_limit
    I, eps = synthetic(), Eps()
    log.info("合成インスタンスを作成: 看護師 %d 人・%d 日、固定割当 %d 件、休み要望 %d 件・シフト要望 %d 件", I.n_nurses, I.n_days,
             len(I.fixed), sum(map(len, I.req_off.values())), sum(map(len, I.req_shift.values())))
    issues = precheck(I, eps)
    log.info("事前検査: %s", f"NG {len(issues)} 件" if issues else "OK")
    if issues:
        print("事前検査NG:", *issues[:10], sep="\n  ")
    res = solve(I, eps, time_limit=time_limit, lin_level=args.lin)
    print(f"lin={args.lin} ok={res.ok} {res.status.exitstatus.name} "
          + " ".join(f"{k}={v:.2f}s" for k, v in res.times.items()))
    print("制約本数:", res.counts)
    if res.ok:
        print(*roster(res.model)[:6], sep="\n")
        log.info("終了: 解が見つかった（リターンコード %d）", ReturnCode.OK)
        return ReturnCode.OK
    log.info("解が見つからない: 診断を開始する")
    d = diagnose(I, eps, time_limit=time_limit, mus_time_limit=mus_time_limit)
    print(d.explain())
    log.info("終了: 診断結果 %s（リターンコード %d）", d.status, int(d.code))
    return d.code


def main(argv: list[str] | None = None) -> int:
    """終了コードを返す。想定した例外（NurseError）は、メッセージとコードだけを出す。想定外の例外は内部エラー（50）

    Args:
        argv: `run` に渡すコマンドライン引数。

    Returns:
        終了コード（`ReturnCode` の整数値）。"""
    try:
        code = run(argv)
    except NurseError as e:
        print(f"エラー: {e}", file=sys.stderr)
        code = code_of(e)
        log.error("エラー終了: %s（%s）", e, type(e).__name__)
    except Exception:
        import traceback
        traceback.print_exc()
        code = ReturnCode.INTERNAL_ERROR
        log.exception("想定外の例外")
    log.info("終了コード: %d %s", int(code), code.name)
    print(f"終了コード: {int(code)} {code.name} — {code.label}")
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
