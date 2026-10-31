"""求解して独立検証器で解を検査する。cpmpy + ortools が必要。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
import nurse_csp as m
import verify


@pytest.mark.parametrize("lin_level", [0, 1])
def test_synthetic_feasible_and_valid(lin_level):
    I, eps = m.synthetic(), m.Eps()
    assert m.precheck(I, eps) == []
    ok, st, M, times, stats = m.solve(I, eps, time_limit=60, lin_level=lin_level)
    if not ok:
        pytest.fail(f"実行不能/時間切れ {st}: 原因候補 {m.diagnose(I, eps)}")
    A = verify.extract(M)
    errs = verify.check(I, eps, A)
    assert errs == [], "\n".join(errs[:20])
    print(lin_level, times, verify.report(I, A))


def test_each_constraint_group_toggle():
    """層2・層3の制約群を1つずつ Rules.off で外しても、構築・求解でき、解が（外した制約を除いて）検証を通ること。
    特に night_band を外したときも 72h の算入判定が健全であること（界が night_band を前提にしない）"""
    import dataclasses as dc
    # 小さめのインスタンス: night_band を外すと 72h の補助変数が全員分になり、既定規模(30人)では求解時間が
    # 数秒〜2分超にばらつく（CLAUDE.md）。ここでは構造の健全性を確かめるのが目的なので 23人・28日で行う
    I0, eps = m.synthetic(20, 3, T=28), m.Eps()
    for name in m.toggleable():
        I = dc.replace(I0, rules=dc.replace(I0.rules, off=frozenset({name})))
        res = m.solve(I, eps, time_limit=120)
        assert res.ok, (name, res.status)
        assert name not in res.model.active
        assert verify.check(I, eps, verify.extract(res.model)) == [], name


@pytest.mark.parametrize("rule,strict", [("weekly", False), ("weekly", True), ("4w4d", False)])
def test_law_holiday_solved_and_verified(rule, strict):
    """層1の休日（労基法35条）: 各規則で求解でき、独立検証器が通る"""
    import dataclasses as dc
    I, eps = m.synthetic(T=31), m.Eps()
    law = dc.replace(I.rules.law, holiday_rule=rule, holiday_strict_calendar=strict)
    I = dc.replace(I, rules=dc.replace(I.rules, law=law))
    ok, st, M, _, stats = m.solve(I, eps, time_limit=60)
    assert ok and stats["holiday"] > 0, st
    A = verify.extract(M)
    assert verify.check(I, eps, A) == []


def test_verify_detects_missing_weekly_holiday():
    """独立検証器が、休日のない週を検出すること（検出力の確認）"""
    I, eps = m.synthetic(T=31), m.Eps()
    ok, _, M, _, _ = m.solve(I, eps, time_limit=60)
    assert ok
    A = verify.extract(M)
    n = 0
    work = next(s for s in I.domain[n] if I.shifts[s].is_work and I.shifts[s].cat == "D")
    for d in range(0, 7):                       # 計画日0〜6は週(月起算)の窓に完全に入る
        A[n][d] = work
    errs = [e for e in verify.check(I, eps, A) if "休日不足" in e]
    assert errs, "休日のない週が検出されなかった"
