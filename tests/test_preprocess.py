"""前処理（時刻→集合・係数）の正しさを総当たりと照合する。ソルバー不要。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import nurse_csp as m

I = m.synthetic()
S = I.shifts
W = [s for s, sd in enumerate(S) if sd.is_work]


def test_night_units_known_values():
    u = {S[s].name: m.night_units(I, s) for s in range(len(S))}
    assert u["日"] == 2 and u["準"] == 17 and u["深"] == 15     # 1h, 8.5h, 7.5h
    assert u["休"] == 0 and u["P4a"] == 0


def test_rest_cliques_exact():
    """閾値分解が禁止ペア集合と完全一致すること"""
    forbidden = {(a, b) for a in W for b in W
                 if S[b].start + m.DAY - S[a].end < I.min_rest}
    covered = {(a, b) for A, B in m.rest_cliques(I) for a in A for b in B}
    assert covered == forbidden


def test_cover_intervals_exact():
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


def test_cover_intervals_sound():
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
