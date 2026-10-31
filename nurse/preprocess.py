"""
前処理（ソルバー外の純Python）: 時刻データから、制約の係数・集合・窓を生成する。
ここの関数は cpmpy に依存せず、tests/test_preprocess.py・tests/test_layers.py で総当たりと照合される。
"""
from __future__ import annotations
from .instance import (Instance, DAY, WEEK, FOUR_WEEKS, HOLIDAY_WEEKLY, HOLIDAY_4W4D,
                       inclusion_threshold)


def night_units(I: Instance, s: int) -> int:
    """u_s: 勤務 s と夜勤時間帯（前日・当日・翌日の窓）の重なり − 申し送り（スロット数）"""
    sd = I.shifts[s]
    if not sd.is_work:
        return 0
    w0, w1 = I.night_window
    ov = sum(max(0, min(sd.end, w1 + k * DAY) - max(sd.start, w0 + k * DAY)) for k in (-1, 0, 1))
    return max(0, ov - I.handover.get(s, 0))


def rest_cliques(I: Instance) -> list[tuple[frozenset, frozenset]]:
    """勤務間隔 ρ の禁止関係を閾値分解した (A_θ, B_θ) の列。
    禁止 ⇔ start_b + 48 − end_a < ρ。θ = end_a ごとに
      A_θ = {a : end_a ≥ θ},  B_θ = {b : start_b + 48 < θ + ρ}
    とすれば x[n,t,A_θ] + x[n,t+1,B_θ] ≤ 1 が全禁止ペアを厳密に表す。
    θ 昇順で B_θ が直前と同じものは支配される（A が小さい）ので除く。"""
    W = [s for s, sd in enumerate(I.shifts) if sd.is_work]
    out, prev_B = [], None
    for th in sorted({I.shifts[a].end for a in W}):
        A = frozenset(a for a in W if I.shifts[a].end >= th)
        B = frozenset(b for b in W if I.shifts[b].start + DAY < th + I.min_rest)
        if A and B and B != prev_B:
            out.append((A, B))
            prev_B = B
    return out


def overnight_shifts(I: Instance) -> list[int]:
    """翌日にかかる勤務（end > 48）の添字。暦日単位の休日の判定に使う"""
    return [s for s, sd in enumerate(I.shifts) if sd.is_work and sd.end > DAY]


def holiday_windows(I: Instance) -> list[tuple[int, int, int]]:
    """労基法35条の休日の窓 (t0, t1, k): 時点 [t0, t1) に休日が k 日以上（時点 t = H + 計画日 d）。
    weekly : 週の起算曜日 week_start から始まる7日の窓。k=1（35条1項）
    4w4d   : four_week_origin から28日ごとの窓。k=4（35条2項）
    計画期間に1日以上かかり、かつ [0, H+T) に完全に収まる窓だけを返す（計画の外にはみ出す窓は検査できない）。
    履歴（前月末）の時点は実績として窓に含まれる。"""
    H, T, law = I.n_history, I.n_days, I.rules.law
    if law.holiday_rule == HOLIDAY_WEEKLY:
        span, need = WEEK, 1
        weekday = lambda t: (I.start_weekday + t - H) % WEEK
        starts = [t for t in range(H + T) if weekday(t) == law.week_start]
    elif law.holiday_rule == HOLIDAY_4W4D:
        span, need = FOUR_WEEKS, 4
        starts = list(range(H + law.four_week_origin, H + T, FOUR_WEEKS))
    else:
        raise ValueError(f"holiday_rule: {law.holiday_rule}")
    return [(t0, t0 + span, need) for t0 in starts if t0 >= 0 and H < t0 + span <= H + T]


def cover_spans(I: Instance, d: int) -> list[tuple[frozenset, int, int, int]]:
    """日 d の人数制約 (C_j, r_j, a_j, b_j) の列。C_j は区間 j を全時間覆う (日オフセット δ∈{−1,0}, s) の集合。
    基本区間に分割 → 同一被覆の統合(max r) → 支配除去（C_i ⊂ C_j かつ r_i ≥ r_j なら j は冗長）。
    [a_j, b_j) は統合した基本区間の最小の始点〜最大の終点（診断の説明文で時間帯を示すために使う）。"""
    W = [s for s, sd in enumerate(I.shifts) if sd.is_work]
    segs = [(off, s, I.shifts[s].start + off * DAY, I.shifts[s].end + off * DAY)
            for off in (-1, 0) for s in W]
    bps = {0, DAY} | {a for a, _, _ in I.demand[d]} | {b for _, b, _ in I.demand[d]}
    bps |= {p for _, _, a, b in segs for p in (a, b) if 0 < p < DAY}
    bps = sorted(bps)
    req = lambda t: max((r for a, b, r in I.demand[d] if a <= t < b), default=0)
    best: dict[frozenset, tuple[int, int, int]] = {}
    for a, b in zip(bps, bps[1:]):
        r = req(a)
        if r <= 0:
            continue
        C = frozenset((off, s) for off, s, sa, sb in segs if sa <= a and b <= sb)
        r0, a0, b0 = best.get(C, (0, a, b))
        best[C] = (max(r0, r), min(a0, a), max(b0, b))
    return [(C, r, a, b) for C, (r, a, b) in best.items()
            if not any(C2 < C and r2 >= r for C2, (r2, _, _) in best.items())]


def cover_intervals(I: Instance, d: int) -> list[tuple[frozenset, int]]:
    """日 d の人数制約 (C_j, r_j) の列（cover_spans から時間帯を除いたもの）"""
    return [(C, r) for C, r, _, _ in cover_spans(I, d)]


def symmetry_classes(I: Instance, mode: str) -> list[list[int]]:
    """同種の看護師の同値類（大きさ 2 以上のものだけ返す）。
    exact  : 取れる値・公休数・資格・夜勤専従・閾値・履歴・固定割当・希望休がすべて一致（入れ替えが解の対称性になる。健全）
    relaxed: 取れる値・公休数・資格・夜勤専従・閾値のみ一致（履歴・固定割当・希望休の違いは無視。入れ替えは
             厳密な対称性ではないため、解を取りこぼす可能性がある不完全な絞り込み）"""
    groups: dict[tuple, list[int]] = {}
    for n in range(I.n_nurses):
        key = (tuple(I.domain[n]), I.off_days[n], n in I.seniors, n in I.night_only, inclusion_threshold(I, n))
        if mode == "exact":
            key += (tuple(int(v) for v in I.history[n]),
                    tuple(sorted((d, s) for (m, d), s in I.fixed.items() if m == n)),
                    tuple(sorted(I.soft_off.get(n, []))))
        groups.setdefault(key, []).append(n)
    return [g for g in groups.values() if len(g) >= 2]


def precheck(I: Instance, eps=None) -> list[str]:
    """L0: 必要条件の集計検査（区間ごとに、覆える値を持ち当日が固定休でない人数 ≥ r_j）。eps は互換のため受け取る"""
    msg = []
    for d in range(I.n_days):
        for C, r in cover_intervals(I, d):
            same_day = {s for off, s in C if off == 0}
            prev_day = {s for off, s in C if off == -1}
            cap = sum(1 for n in range(I.n_nurses)
                      if any(s in I.domain[n] for s in same_day | prev_day)
                      and (I.fixed.get((n, d)) is None or I.fixed[(n, d)] in same_day))
            if cap < r:
                msg.append(f"day{d}: 区間需要{r} > 供給可能{cap}")
    return msg
