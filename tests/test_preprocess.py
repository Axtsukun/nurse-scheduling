"""前処理（時刻→集合・係数）の正しさを総当たりと照合する。ソルバー不要。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import nurse_csp as m
from nurse.preprocess import cover_spans

I = m.synthetic()
S = I.shifts
W = [s for s, sd in enumerate(S) if sd.is_work]


def test_night_units_known_values() -> None:
    u = {S[s].name: m.night_units(I, s) for s in range(len(S))}
    assert u["日"] == 2 and u["準"] == 17 and u["深"] == 15     # 1h, 8.5h, 7.5h
    assert u["休"] == 0 and u["P4a"] == 0


def test_rest_cliques_exact() -> None:
    """閾値分解が禁止ペア集合と完全一致すること"""
    forbidden = {(a, b) for a in W for b in W
                 if S[b].start + m.DAY - S[a].end < I.min_rest}
    covered = {(a, b) for A, B in m.rest_cliques(I) for a in A for b in B}
    assert covered == forbidden


def test_cover_intervals_exact() -> None:
    """支配除去後の制約群が、30分スロットごとの制約をすべて含意すること（厳密性）"""
    for d in range(I.n_days):
        kept = m.cover_intervals(I, d)
        for slot in range(m.DAY):
            r = max((r for a, b, r in I.demand[d] if a <= slot < b), default=0)
            if r == 0:
                continue
            C_slot = frozenset((off, s) for off in (-1, 0) for s in W
                               if S[s].start + off * m.DAY <= slot and slot + 1 <= S[s].end + off * m.DAY)
            # ある保持制約 (C, r') が C ⊆ C_slot かつ r' ≥ r なら、スロット制約は含意される
            assert any(C <= C_slot and rr >= r for C, rr in kept), (d, slot)


def test_cover_intervals_sound() -> None:
    """保持制約がスロット制約より強すぎない（各保持制約がある区間の需要そのもの）こと"""
    for d in range(I.n_days):
        for C, r in m.cover_intervals(I, d):
            ok = False
            for slot in range(m.DAY):
                rs = max((r2 for a, b, r2 in I.demand[d] if a <= slot < b), default=0)
                C_slot = frozenset((off, s) for off in (-1, 0) for s in W
                                   if S[s].start + off * m.DAY <= slot and slot + 1 <= S[s].end + off * m.DAY)
                if C_slot <= C and rs >= r:        # このスロット制約が保持制約を含意
                    ok = True
                    break
            assert ok, (d, sorted(C), r)


def test_cover_spans_are_base_intervals_and_do_not_overlap() -> None:
    """人数制約の区間は基本区間そのもの（統合しない）。互いに重ならず、それぞれ1つの需要区間に収まる。
    要求人数はその区間の需要と一致する（番の中で一定）"""
    for d in range(I.n_days):
        spans = sorted((a, b, r) for _, r, a, b in cover_spans(I, d))
        assert all(b1 <= a2 for (_, b1, _), (a2, _, _) in zip(spans, spans[1:])), (d, spans)
        for a, b, r in spans:
            assert any(da <= a and b <= db and r == dr for da, db, dr in I.demand[d]), (d, a, b, r)


def test_demand_must_cover_the_day_without_gaps_or_overlaps() -> None:
    """需要は番ごとに一定で、1日（0〜DAY）を空きなく重なりなく覆う。需要なしの時間帯は r=0 で明示する"""
    import dataclasses as dc
    import pytest
    ok = [(0, 17, 3), (17, 31, 0), (31, m.DAY, 4)]                       # r=0 の番は許す
    bad = {"空き": [(0, 10, 3), (12, m.DAY, 3)], "先頭が0でない": [(2, m.DAY, 3)], "終端がDAYでない": [(0, 40, 3)],
           "重なり": [(0, 30, 3), (20, m.DAY, 3)], "逆順": [(0, 0, 3), (0, m.DAY, 3)], "負の人数": [(0, m.DAY, -1)]}
    dc.replace(I, demand=[ok] * I.n_days)
    for name, bands in bad.items():
        with pytest.raises(ValueError):
            dc.replace(I, demand=[bands] * I.n_days)
    with pytest.raises(ValueError):                                       # 日数が違う
        dc.replace(I, demand=[ok] * (I.n_days - 1))
