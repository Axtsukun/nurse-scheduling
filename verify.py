"""独立検証器: ソルバーの解（割当表）を、モデルの式を使わずに元の規則で検査する。
モデル側の定式化の誤り（閾値分解・支配除去・片側定義・補助変数による窓など）を検出するのが目的。

`A[n][d]` = 計画日 d に看護師 n が就く勤務の添字 s（𝒮 の添字）

検査は制約名ごとの関数（CHECKS）に分けてあり、名前は registry の制約名と一致する。

- 制約を足したら、同じ名前の検査関数を足す（tests/test_layers.py が対応漏れを検出する）
- Rules.off でオフにした制約の検査は行わない（check の off 引数で追加指定もできる）"""
from __future__ import annotations
import math
from typing import Callable
from nurse import (Instance, Eps, Assignment, DAY, WEEK, FOUR_WEEKS, OFF_NAME, HOLIDAY_WEEKLY, REGISTRY, MANDATORY,
                   night_units, night_band, night_capable, is_part, weekend_days, weekend_pairs,
                   inclusion_threshold, full_sequences, staff_on_duty)

Check = Callable[[Instance, Eps, Assignment, list], list]       # (I, eps, A, full) -> エラー文のリスト


def extract(M) -> Assignment:
    """NurseModel の解から割当表 A を取り出す（x のみ使用）"""
    A = []
    for n in M.N:
        row = []
        for d in range(M.T):
            vals = [int(v.value()) for v in M.xv[n][M.t(d), :]]
            if sum(vals) != 1:
                raise RuntimeError(f"n{n} d{d}: 1日1値に違反")
            row.append(M.dom[n][vals.index(1)])
        A.append(row)
    return A


# =============================================================================
# 検査（制約名ごと）。full = 履歴を前置した勤務列、時点 t = H + 計画日 d
# =============================================================================
def check_fixed(I, eps, A, full):
    """ドメイン・固定割当・fixed_only の値"""
    S, err = I.shifts, []
    for n in range(I.n_nurses):
        for d, s in enumerate(A[n]):
            fx = I.fixed.get((n, d))
            if fx is not None and s != fx:
                err.append(f"n{n} d{d}: 固定 {S[fx].name} ≠ {S[s].name}")
            if fx is None and s not in I.domain[n]:
                err.append(f"n{n} d{d}: ドメイン外 {S[s].name}")
            if S[s].fixed_only and fx != s:
                err.append(f"n{n} d{d}: {S[s].name} は固定日以外不可")
    return err


def check_holiday(I, eps, A, full):
    """労基法35条の休日（週の分割・4週ブロックの分割から直接数える。休日 = 「休」の日、有休は数えない）"""
    S, H, T, law, err = I.shifts, I.n_history, I.n_days, I.rules.law, []
    for n in range(I.n_nurses):
        seq = full[n]

        def is_holiday(t):
            if S[seq[t]].name != OFF_NAME:
                return False
            if law.holiday_strict_calendar and t > 0:
                prev = S[seq[t - 1]]
                if prev.is_work and prev.end > DAY:        # 前日の勤務が日をまたいで食い込む
                    return False
            return True

        blocks: dict[int, list[int]] = {}
        if law.holiday_rule == HOLIDAY_WEEKLY:
            need, size = 1, WEEK
            for t in range(H + T):
                blocks.setdefault((t - H + I.start_weekday - law.week_start) // WEEK, []).append(t)
        else:
            need, size = 4, FOUR_WEEKS
            for t in range(H + law.four_week_origin, H + T):
                blocks.setdefault((t - H - law.four_week_origin) // FOUR_WEEKS, []).append(t)
        for ts in blocks.values():
            if len(ts) < size or ts[-1] < H:               # 計画の外にはみ出す窓・履歴だけの窓は対象外
                continue
            got = sum(is_holiday(t) for t in ts)
            if got < need:
                err.append(f"n{n}: 休日不足 {law.holiday_rule} 窓 t{ts[0]}-t{ts[-1]}: {got} < {need}")
    return err


def check_rest(I, eps, A, full):
    """勤務間隔（全ての連続2日を時刻で直接判定）"""
    S, H, T, err = I.shifts, I.n_history, I.n_days, []
    for n in range(I.n_nurses):
        for t in range(H - 1, H + T - 1):
            a, b = S[full[n][t]], S[full[n][t + 1]]
            if a.is_work and b.is_work and b.start + DAY - a.end < I.min_rest:
                err.append(f"n{n} t{t}: 勤務間隔 {a.name}→{b.name} = {(b.start + DAY - a.end) / 2}h")
    return err


def check_consec(I, eps, A, full):
    """連続勤務日数（履歴からの連続を含む）"""
    S, H, err = I.shifts, I.n_history, []
    for n in range(I.n_nurses):
        run = 0
        for t, s in enumerate(full[n]):
            run = run + 1 if S[s].is_work else 0
            if t >= H and run > I.max_consec_work:
                err.append(f"n{n} t{t}: 連続勤務 {run} > {I.max_consec_work}")
    return err


def check_offblock(I, eps, A, full):
    """非勤務ブロック長 ≥ m"""
    S, H, err = I.shifts, I.n_history, []
    if I.min_off_block <= 1:
        return err
    for n in range(I.n_nurses):
        seq, t = full[n], 1
        while t < len(seq):
            if not S[seq[t]].is_work and S[seq[t - 1]].is_work:
                j = t
                while j < len(seq) and not S[seq[j]].is_work:
                    j += 1
                if j < len(seq) and j - t < I.min_off_block and j - 1 >= H:
                    err.append(f"n{n} t{t}: 休みブロック長 {j - t} < {I.min_off_block}")
                t = j
            else:
                t += 1
    return err


def check_offcount(I, eps, A, full):
    """公休数 = F_n"""
    S, err = I.shifts, []
    for n in range(I.n_nurses):
        off = sum(1 for s in A[n] if S[s].name == OFF_NAME)
        if off != I.off_days[n]:
            err.append(f"n{n}: 公休 {off} ≠ {I.off_days[n]}")
    return err


def check_weekend(I, eps, A, full):
    """勤務週末数 ≤ W（週末 = 土曜と日曜の組。どちらかに勤務すれば1回）"""
    S, err = I.shifts, []
    for n in range(I.n_nurses):
        we = sum(1 for sat, sun in weekend_pairs(I) if S[A[n][sat]].is_work or S[A[n][sun]].is_work)
        if we > I.max_work_weekends:
            err.append(f"n{n}: 勤務週末 {we} > {I.max_work_weekends}")
    return err


def check_night_band(I, eps, A, full):
    """夜勤回数が帯 [lo,hi] に入る（夜勤可能・非専従者）"""
    S, err = I.shifts, []
    lo, hi = night_band(I, eps)
    for n in range(I.n_nurses):
        if night_capable(I, n) and n not in I.night_only:
            c = sum(1 for s in A[n] if S[s].cat in I.night_cats)
            if not lo <= c <= hi:
                err.append(f"n{n}: 夜勤回数 {c} ∉ [{lo},{hi}]")
    return err


def check_cover(I, eps, A, full):
    """人数（30分スロットごとに直接数える。基本区間・支配除去を使わない）"""
    err = []
    for d in range(I.n_days):
        for slot in range(DAY):
            r = max((r for a, b, r in I.demand[d] if a <= slot < b), default=0)
            if r == 0:
                continue
            cnt = staff_on_duty(I, full, d, slot)
            if cnt < r:
                err.append(f"d{d} {slot // 2:02d}:{30 * (slot % 2):02d}: 在席 {cnt} < {r}")
    return err


def check_skill(I, eps, A, full):
    """夜勤リーダー資格者が各日・各区分に1人以上"""
    S, err = I.shifts, []
    for d in range(I.n_days):
        for k in I.leader_cats:
            if not any(S[A[n][d]].cat == k for n in I.seniors):
                err.append(f"d{d}: リーダー不在 {k}")
    return err


def check_72h(I, eps, A, full):
    """72時間ルール（定義どおり: 算入者の延べ夜勤時間 / 算入者数）"""
    tot, cnt = 0, 0
    for n in range(I.n_nurses):
        if n in I.night_only:
            continue
        h = sum(night_units(I, s) for s in A[n])
        if h >= inclusion_threshold(I, n):
            tot += h
            cnt += 1
    if cnt and tot > I.limit_units * cnt + I.budget_units:
        return [f"72h: 平均 {tot / cnt / 2:.1f}h > {I.limit_units / 2}h"]
    return []


def check_weekend_spread(I, eps, A, full):
    """土日勤務日数の max−min が区分(正規/パート)内で上限以下"""
    S, err = I.shifts, []
    sat, sun = weekend_days(I)
    e = [sum(S[A[n][d]].is_work for d in sat + sun) for n in range(I.n_nurses)]
    for flag, delta, label in ((False, eps.weekend_spread_regular, "正規"), (True, eps.weekend_spread_part, "パート")):
        g = [e[n] for n in range(I.n_nurses) if is_part(I, n) == flag]
        if g and max(g) - min(g) > delta:
            err.append(f"土日勤務日数 {label}: max−min = {max(g) - min(g)} > {delta}")
    return err


def check_soft_off(I, eps, A, full):
    """希望休の違反数 ≤ κ"""
    S = I.shifts
    viol = sum(1 for n, ds in I.soft_off.items() for d in ds if S[A[n][d]].name != OFF_NAME)
    return [f"希望休違反 {viol} > {eps.soft_off_viol}"] if viol > eps.soft_off_viol else []


def check_req_off(I, eps, A, full):
    """休み要望の違反数 ≤ ⌊ρ·要望数⌋"""
    S = I.shifts
    cells = [(n, d) for n, ds in I.req_off.items() for d in ds]
    if not cells:
        return []
    viol, cap = sum(1 for n, d in cells if S[A[n][d]].name != OFF_NAME), math.floor(eps.req_viol_ratio * len(cells))
    return [f"休み要望違反 {viol} > {cap}"] if viol > cap else []


def check_req_shift(I, eps, A, full):
    """シフト要望の違反数 ≤ ⌊ρ·要望数⌋"""
    cells = [(n, d, s) for n, ds in I.req_shift.items() for d, s in ds]
    if not cells:
        return []
    viol, cap = sum(1 for n, d, s in cells if A[n][d] != s), math.floor(eps.req_viol_ratio * len(cells))
    return [f"シフト要望違反 {viol} > {cap}"] if viol > cap else []


# 制約名 → 検査。registry の制約名と一致させる（struct・history は extract が、sym_break は技法なので検査対象外）
CHECKS: dict[str, Check] = {
    "fixed": check_fixed, "holiday": check_holiday,
    "rest": check_rest, "consec": check_consec, "offblock": check_offblock,
    "offcount": check_offcount, "weekend": check_weekend, "night_band": check_night_band,
    "cover": check_cover, "skill": check_skill, "72h": check_72h,
    "weekend_spread": check_weekend_spread,
    "soft_off": check_soft_off, "req_off": check_req_off, "req_shift": check_req_shift,
}


def check(I: Instance, eps: Eps, A: Assignment, off: frozenset | set = frozenset()) -> list[str]:
    """解 A を検査してエラー文のリストを返す（空なら全て充足）。
    Rules.off と off 引数で指定した制約の検査は行わない（カーネル・層1は外せない）。"""
    skipped = set(I.rules.off) | set(off)
    bad = [nm for nm in skipped if nm in REGISTRY and REGISTRY[nm].scope in MANDATORY]
    if bad:
        raise ValueError(f"オフにできない制約の検査を外そうとした: {bad}")
    full = full_sequences(I, A)
    errs: list[str] = []
    for name, fn in CHECKS.items():
        if name not in skipped:
            errs += fn(I, eps, A, full)
    return errs


def report(I: Instance, A: Assignment) -> dict:
    """解の要約（72h平均など）"""
    hs = {n: sum(night_units(I, s) for s in A[n]) for n in range(I.n_nurses) if n not in I.night_only}
    inc = [h for n, h in hs.items() if h >= inclusion_threshold(I, n)]
    return dict(avg_night_hours=(sum(inc) / len(inc) / 2) if inc else 0.0,
                included=len(inc), excluded=len(hs) - len(inc))
