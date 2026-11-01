"""
求解結果の評価: 解（割当表 A）を集計して表示する。制約の充足判定は verify.py、ここは分布の把握が目的。

  1. 日 × 時間帯の要求数と在席数、日 × 区分(日/準/深/パート)の割当数
  2. 正規職員の勤務回数・勤務時間・夜勤回数の統計量
  3. パート職員の同上
  4. 72時間ルールでの平均夜勤時間（算入者の延べ夜勤時間 / 算入者数）
  5. 土日勤務の偏り（人ごとの土日勤務日数・勤務週末数・平日との勤務率の差）
  6. 要望の充足（既存の希望休・休み要望・シフト要望の件数と充足率）

使い方: python evaluate.py        （合成インスタンスを求解して評価）
"""
from __future__ import annotations
import statistics as st
from nurse import (Instance, Eps, Assignment, OFF_NAME, night_units, solve, synthetic, is_part, weekend_days,
                   weekend_pairs, with_requests, inclusion_threshold, full_sequences, staff_on_duty)
import verify


def _fmt_hm(h: int) -> str:
    """スロット番号を `HH:MM`（ゼロ埋め）にする。

    Args:
        h: 30分単位のスロット番号（0 = 0:00、48 = 翌日 0:00）。

    Returns:
        `HH:MM` 形式の時刻文字列。"""
    return f"{h // 2:02d}:{30 * (h % 2):02d}"


def _summary(values: list) -> dict:
    """人数・min・max・平均・標準偏差（母集団）・中央値。空なら空の辞書

    Args:
        values: 集計する数値のリスト。

    Returns:
        `n`（人数）・`min`・`max`・`mean`・`sd`（母集団の標準偏差）・`median` の辞書。空なら空の辞書。"""
    if not values:
        return {}
    return dict(n=len(values), min=min(values), max=max(values), mean=st.fmean(values),
                sd=st.pstdev(values), median=st.median(values))


# =============================================================================
# 集計（表示しない。値を辞書で返す）
# =============================================================================
def coverage(I: Instance, A: Assignment) -> list[dict]:
    """日 d・需要区間 (a,b,r) ごとの要求数 r と、区間内の最小在席数（30分スロット単位で直接数える）。
    併せて、日 d に始まる勤務の区分別人数。

    Args:
        I: 入力データ。
        A: 割当表。`A[n][d]` は計画日 d に看護師 n が就く勤務の添字 s。

    Returns:
        日ごとの辞書のリスト。`day`（計画日）、`bands`（`(a, b, 要求数, 最小在席数)` の並び）、`by_cat`（その日に始まる勤務の区分別人数）を持つ。"""
    S = I.shifts
    full = full_sequences(I, A)
    cats = sorted({sd.cat for sd in S if sd.is_work})
    rows = []
    for d in range(I.n_days):
        bands = [(a, b, r, min(staff_on_duty(I, full, d, t) for t in range(a, b))) for a, b, r in I.demand[d]]
        by_cat = {k: sum(1 for n in range(I.n_nurses) if S[A[n][d]].cat == k) for k in cats}
        rows.append(dict(day=d, bands=bands, by_cat=by_cat))
    return rows


def person_stats(I: Instance, A: Assignment, members: list[int]) -> dict:
    """勤務回数(日数)・勤務時間(h)・夜勤回数(夜勤区分の日数)の統計量

    Args:
        I: 入力データ。
        A: 割当表。`A[n][d]` は計画日 d に看護師 n が就く勤務の添字 s。
        members: 集計する看護師の添字のリスト。

    Returns:
        `勤務回数`・`勤務時間h`・`夜勤回数` をキーに、それぞれの統計量（`_summary` の形）を値にした辞書。"""
    S = I.shifts
    vals: dict[str, list[float]] = {
        "勤務回数": [sum(S[s].is_work for s in A[n]) for n in members],
        "勤務時間h": [sum((S[s].end - S[s].start) for s in A[n] if S[s].is_work) / 2 for n in members],
        "夜勤回数": [sum(S[s].cat in I.night_cats for s in A[n]) for n in members],
    }
    return {k: _summary(v) for k, v in vals.items()}


def night72(I: Instance, A: Assignment) -> dict:
    """72時間ルール: 算入者（夜勤専従を除き、夜勤時間 h_n ≥ θ_n の者）の平均夜勤時間と上限。
    上限は Λ + q/（算入者数）を時間に直した値。算入前の全員平均・区分別平均も併記する。

    Args:
        I: 入力データ。
        A: 割当表。`A[n][d]` は計画日 d に看護師 n が就く勤務の添字 s。

    Returns:
        `avg_included`（算入者の平均夜勤時間）・`limit`（上限）・`n_included`・`n_excluded`・`avg_all`・`avg_regular`・`avg_part`・`max_included`・`min_included` を持つ辞書（時間の単位は h）。"""
    h = {n: sum(night_units(I, s) for s in A[n]) for n in range(I.n_nurses) if n not in I.night_only}
    inc = [n for n in h if h[n] >= inclusion_threshold(I, n)]
    avg = lambda ns: (sum(h[n] for n in ns) / len(ns) / 2) if ns else float("nan")
    part = [n for n in h if is_part(I, n)]
    reg = [n for n in h if not is_part(I, n)]
    return dict(avg_included=avg(inc), limit=I.limit_units / 2 + I.budget_units / 2 / max(len(inc), 1),
                n_included=len(inc), n_excluded=len(h) - len(inc),
                avg_all=avg(list(h)), avg_regular=avg(reg), avg_part=avg(part),
                max_included=max((h[n] for n in inc), default=0) / 2,
                min_included=min((h[n] for n in inc), default=0) / 2)


def weekend_stats(I: Instance, A: Assignment) -> dict:
    """人ごとの土日勤務。勤務週末数は制約 weekend と同じ定義（weekend_pairs: 土曜と日曜のどちらかに勤務）。
    勤務率差 = 土日の勤務率 − 平日の勤務率（正なら土日に偏って勤務）。休・有休は非勤務として数える。

    Args:
        I: 入力データ。
        A: 割当表。`A[n][d]` は計画日 d に看護師 n が就く勤務の添字 s。

    Returns:
        `rows`（人ごとの土日勤務）・`groups`（正規/パート別の統計量）・`n_sat`・`n_sun`（土日の日数）・`limit`（勤務週末数の上限）を持つ辞書。"""
    S, T = I.shifts, I.n_days
    sat, sun = weekend_days(I)
    we = set(sat) | set(sun)
    wk = [d for d in range(T) if d not in we]
    w = lambda n, d: S[A[n][d]].is_work
    rows = []
    for n in range(I.n_nurses):
        r = dict(n=n, part=is_part(I, n), sat=sum(w(n, d) for d in sat), sun=sum(w(n, d) for d in sun),
                 weekends=sum(1 for a, b in weekend_pairs(I) if w(n, a) or w(n, b)),
                 rate_we=sum(w(n, d) for d in we) / len(we) if we else 0.0,
                 rate_wk=sum(w(n, d) for d in wk) / len(wk) if wk else 0.0)
        r["days"] = r["sat"] + r["sun"]
        r["diff"] = r["rate_we"] - r["rate_wk"]
        rows.append(r)
    groups = {key: {k: _summary([r[k] for r in rows if r["part"] == flag]) for k in ("days", "weekends", "diff")}
              for key, flag in (("regular", False), ("part", True))}
    return dict(rows=rows, groups=groups, n_sat=len(sat), n_sun=len(sun), limit=I.max_work_weekends)


def request_stats(I: Instance, A: Assignment) -> dict:
    """要望ごとの 件数・充足数・充足率（充足 = 休み要望は「休」、シフト要望は指定の勤務に就く）

    Args:
        I: 入力データ。
        A: 割当表。`A[n][d]` は計画日 d に看護師 n が就く勤務の添字 s。

    Returns:
        `希望休(既存)`・`休み要望`・`シフト要望` をキーに、`total`（件数）・`met`（充足数）・`rate`（充足率）を持つ辞書を値にした辞書。"""
    S = I.shifts
    off = [S[A[n][d]].name == OFF_NAME for n, ds in I.soft_off.items() for d in ds]
    roff = [S[A[n][d]].name == OFF_NAME for n, ds in I.req_off.items() for d in ds]
    rsh = [A[n][d] == s for n, ds in I.req_shift.items() for d, s in ds]
    pack = lambda v: dict(total=len(v), met=sum(v), rate=(sum(v) / len(v)) if v else float("nan"))
    return {"希望休(既存)": pack(off), "休み要望": pack(roff), "シフト要望": pack(rsh)}


def evaluate(I: Instance, A: Assignment) -> dict:
    """全ての集計をまとめて返す

    Args:
        I: 入力データ。
        A: 割当表。`A[n][d]` は計画日 d に看護師 n が就く勤務の添字 s。

    Returns:
        `coverage`・`regular`・`part`・`night72`・`weekend`・`requests`・`n_regular`・`n_part` を持つ辞書（`print_report` に渡せる）。"""
    reg = [n for n in range(I.n_nurses) if not is_part(I, n)]
    prt = [n for n in range(I.n_nurses) if is_part(I, n)]
    return dict(coverage=coverage(I, A), regular=person_stats(I, A, reg), part=person_stats(I, A, prt),
                night72=night72(I, A), weekend=weekend_stats(I, A), requests=request_stats(I, A),
                n_regular=len(reg), n_part=len(prt))


# =============================================================================
# 表示（節ごと）
# =============================================================================
def _print_coverage(cov: list[dict]) -> None:
    """1. 日別の要求数/最小在席数（時間帯ごと）と、区分別の割当数を表示する。

    Args:
        cov: `coverage` の結果。"""
    cats = list(cov[0]["by_cat"])
    print("■ 1. 日別: 要求数/最小在席数（時間帯）と区分別割当数")
    heads = [f"{_fmt_hm(a)}-{_fmt_hm(b)}" for a, b, _, _ in cov[0]["bands"]]
    print(f"{'日':>3} " + " ".join(f"{h:>13}" for h in heads) + "  | " + " ".join(f"{k:>3}" for k in cats))
    short = 0
    for r in cov:
        cells = []
        for a, b, req, pres in r["bands"]:
            short += pres < req
            cells.append(f"{req:>3}/{pres:<3}{'  ' if pres >= req else ' ✗'}   ")
        print(f"{r['day']:>3} " + " ".join(f"{c:>13}" for c in cells) + "  | "
              + " ".join(f"{r['by_cat'][k]:>3}" for k in cats))
    print(f"  不足した（日,時間帯）数: {short}")
    print("  区分別延べ割当: " + ", ".join(f"{k}={sum(r['by_cat'][k] for r in cov)}" for k in cats))


def _print_person_stats(title: str, stats: dict, count: int) -> None:
    """2・3. 正規職員またはパート職員の統計量（勤務回数・勤務時間・夜勤回数）を表示する。

    Args:
        title: 節の見出し。
        stats: `person_stats` の結果。
        count: 対象の人数。"""
    print(f"\n■ {title}（{count}人）")
    print(f"{'指標':<10}{'min':>7}{'max':>7}{'mean':>8}{'sd':>7}{'median':>8}")
    for k, v in stats.items():
        if v:
            print(f"{k:<10}{v['min']:>7.1f}{v['max']:>7.1f}{v['mean']:>8.2f}{v['sd']:>7.2f}{v['median']:>8.1f}")


def _print_night72(n: dict) -> None:
    """4. 72時間ルール（月平均夜勤時間）を表示する。

    Args:
        n: `night72` の結果。"""
    print("\n■ 4. 72時間ルール（月平均夜勤時間）")
    print(f"  算入者の平均 {n['avg_included']:.2f}h / 上限 {n['limit']:.2f}h"
          f"（算入 {n['n_included']}人・除外 {n['n_excluded']}人、算入者の範囲 {n['min_included']:.1f}〜{n['max_included']:.1f}h）")
    print(f"  参考: 全員平均 {n['avg_all']:.2f}h / 正規 {n['avg_regular']:.2f}h / パート {n['avg_part']:.2f}h")


def _print_weekend(wk: dict) -> None:
    """5. 土日勤務の偏り（区分別の統計量、日数の分布、人別の内訳）を表示する。

    Args:
        wk: `weekend_stats` の結果。"""
    print(f"\n■ 5. 土日勤務の偏り（土曜{wk['n_sat']}日・日曜{wk['n_sun']}日、勤務週末数の上限 {wk['limit']}）")
    print(f"{'区分':<8}{'指標':<10}{'min':>7}{'max':>7}{'mean':>8}{'sd':>7}")
    for key, label in (("regular", "正規"), ("part", "パート")):
        for k, name in (("days", "土日勤務日数"), ("weekends", "勤務週末数"), ("diff", "勤務率差")):
            v = wk["groups"][key].get(k)
            if v:
                print(f"{label:<8}{name:<10}{v['min']:>7.2f}{v['max']:>7.2f}{v['mean']:>8.2f}{v['sd']:>7.2f}")
    for flag, label in ((False, "正規"), (True, "パート")):
        cnt: dict[int, int] = {}
        for r in wk["rows"]:
            if r["part"] == flag:
                cnt[r["days"]] = cnt.get(r["days"], 0) + 1
        print(f"  {label} 土日勤務日数の分布: " + ", ".join(f"{k}日={c}人" for k, c in sorted(cnt.items())))
    print("  人別（土, 日, 勤務週末, 土日勤務率, 平日勤務率）:")
    for r in wk["rows"]:
        print(f"   N{r['n']:02d}{'P' if r['part'] else ' '} {r['sat']} {r['sun']} {r['weekends']}"
              f"  {r['rate_we']:.2f} {r['rate_wk']:.2f}")


def _print_requests(req: dict) -> None:
    """6. 要望の充足（件数・充足数・充足率）を表示する。

    Args:
        req: `request_stats` の結果。"""
    print("\n■ 6. 要望の充足")
    for k, v in req.items():
        print(f"  {k:<10} {v['met']:>3}/{v['total']:<3} = {v['rate']:.1%}" if v["total"] else f"  {k:<10} なし")


def print_report(I: Instance, ev: dict) -> None:
    """evaluate の結果を節ごとに表示する

    Args:
        I: 入力データ（現在は表示に使わない）。
        ev: `evaluate` の結果。"""
    _print_coverage(ev["coverage"])
    _print_person_stats("2. 正規職員", ev["regular"], ev["n_regular"])
    _print_person_stats("3. パート職員", ev["part"], ev["n_part"])
    _print_night72(ev["night72"])
    _print_weekend(ev["weekend"])
    _print_requests(ev["requests"])


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="合成インスタンス1問を求解し、検証と評価を表示する")
    ap.add_argument("--lin", type=int, default=1, help="linearization_level（0/1/2）")
    ap.add_argument("--time-limit", type=float, default=30)
    ap.add_argument("--requests", type=float, default=0.0, help="個別要望の密度（例 0.10 = 全セルの1割を追加）")
    args = ap.parse_args()
    I, eps = synthetic(), Eps()
    if args.requests:
        I = with_requests(I, args.requests)
    res = solve(I, eps, time_limit=args.time_limit, lin_level=args.lin)
    print(f"求解: ok={res.ok} {res.status.exitstatus.name} lin={args.lin} solve={res.times['solve']:.2f}s")
    if res.ok:
        A = verify.extract(res.model)
        print("verify.check:", verify.check(I, eps, A) or "OK")
        print_report(I, evaluate(I, A))
