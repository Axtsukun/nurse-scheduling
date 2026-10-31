"""診断（diagnose）: 解けない原因を「時間切れ」と「制約矛盾」に分け、矛盾なら MUS を日本語で説明する。
hard / soft の分け方: 背景（構造・履歴・補助変数の定義）と前提（カーネル・層1）は hard、層2・層3 は soft。"""
import dataclasses as dc
import re
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import cpmpy as cp
import pytest
import nurse_csp as m


def _inst_offcount():
    """公休が日数を超える（層2 の offcount 単独の矛盾。前提は不要）"""
    I = m.synthetic()
    return dc.replace(I, off_days=[I.n_days + 1] + I.off_days[1:])


def _inst_fixed_week_consec():
    """看護師3を6日目(土)から7日間ずっと日勤に固定（カーネル）→ 連続勤務5日以内（層3）と矛盾"""
    I = m.synthetic()
    fx = dict(I.fixed)
    for d in range(5, 12):
        fx[(3, d)] = I.idx("日")
    return dc.replace(I, fixed=fx)


def _inst_fixed_week_holiday():
    """月〜日の1週間ずっと日勤に固定（カーネル）。連続勤務をオフにすると、週1休（層1）と矛盾 = hard 同士の矛盾"""
    I = m.synthetic()
    fx = dict(I.fixed)
    for d in range(7, 14):
        fx[(3, d)] = I.idx("日")
    return dc.replace(I, fixed=fx, rules=dc.replace(I.rules, off=frozenset({"consec"})))


def _is_sat(hard, soft):
    return cp.Model(hard + soft).solve("ortools")


def test_feasible():
    d = m.diagnose(m.synthetic(), m.Eps(), time_limit=30)
    assert d.status == m.FEASIBLE and not d.items
    assert "矛盾はありません" in d.explain()


def test_timeout_is_not_reported_as_conflict():
    """時間切れでは、制約矛盾かどうか不明なので MUS を求めず TIMEOUT を返す"""
    d = m.diagnose(m.synthetic(52, 8, T=31), m.Eps(), time_limit=0.05)
    assert d.status == m.TIMEOUT and not d.items
    assert "時間切れ" in d.explain() and "矛盾かどうかは不明" in d.explain()


def test_soft_only_conflict_needs_no_given_constraint():
    d = m.diagnose(_inst_offcount(), m.Eps(), time_limit=60)
    assert d.status == m.CONFLICT and not d.hard_only and d.mus_solver == "exact"
    assert d.groups == ["offcount"] and not d.given_items
    assert [x.line for x in d.soft_items] == ["offcount-0001  [公休数一致] N00 公休数=31"]
    text = d.explain()
    assert "制約矛盾" in text and "offcount-0001  [公休数一致] N00 公休数=31" in text and "層2" in text
    assert "前提としている制約" not in text                          # 前提が不要なら、その欄は出さない


def test_soft_conflict_names_the_given_constraints_it_depends_on():
    """固定割当（カーネル・hard）と連続勤務（層3・soft）: soft は連続勤務の1窓、前提は窓内の固定割当6件だけ"""
    d = m.diagnose(_inst_fixed_week_consec(), m.Eps(), time_limit=60)
    assert d.status == m.CONFLICT and not d.hard_only and d.groups == ["fixed", "consec"]
    assert [x.group for x in d.soft_items] == ["consec"]
    assert d.soft_items[0].message == "N03 6日目(土) - 11日目(木) 連続勤務日数=5"
    assert len(d.given_items) == 6 and all(x.message.startswith("N03 ") and x.message.endswith(" 勤務=日") for x in d.given_items)
    text = d.explain()
    assert "[固定割当] N03 6日目(土) 勤務=日" in text and "[連続勤務日数上限] N03 6日目(土) - 11日目(木) 連続勤務日数=5" in text
    assert "前提としている制約（オフにできない。カーネル・層1）" in text and "矛盾の原因になっている制約（外せる。層2・層3）" in text
    assert "入力データ（固定割当など）を見直す手もあります" in text


def test_hard_only_conflict_is_diagnosed_separately():
    """固定割当（カーネル）と週1休（層1）はどちらもオフにできない → soft の MUS は空 → 前提同士の矛盾として説明"""
    d = m.diagnose(_inst_fixed_week_holiday(), m.Eps(), time_limit=60)
    assert d.status == m.CONFLICT and d.hard_only and d.groups == ["fixed", "holiday"]
    assert not d.soft_items and len(d.given_items) == 8
    days = [int(re.search(r"(\d+)日目", x.message).group(1)) for x in d.given_items if x.group == "fixed"]
    assert days == sorted(days) == list(range(8, 15))                    # 固定割当は日付順に並ぶ
    assert sum(x.group == "fixed" for x in d.items) == 7 and sum(x.group == "holiday" for x in d.items) == 1
    text = d.explain()
    assert "オフにできない制約同士" in text and "オフにできません" in text and "Rules.off" not in text


@pytest.mark.parametrize("make", [_inst_offcount, _inst_fixed_week_consec, _inst_fixed_week_holiday])
def test_mus_is_unsat_and_minimal(make):
    """MUS（前提 + soft）= それ自体は充足不能で、どの1件を除いても充足可能（極小）。独立に組み直したモデルで確かめる"""
    I, eps = make(), m.Eps()
    d = m.diagnose(I, eps, time_limit=60)
    named = [x for items in m.NurseModel(I, eps).build_named().values() for x in items]
    background = [x.expr for x in named if x.hard]
    ids = {x.id for x in d.items}                                         # ID は同じ入力なら同じ → 組み直したモデルと照合できる
    chosen = [x for x in named if x.id in ids]
    assert len(chosen) == len(d.items) and not any(x.hard for x in chosen)
    assert not _is_sat(background, [x.expr for x in chosen])              # 矛盾している
    for i in range(len(chosen)):
        rest = [x.expr for j, x in enumerate(chosen) if j != i]
        assert _is_sat(background, rest), chosen[i].id                    # 1件外せば解ける


def test_solver_choice_is_fixed_cpsat_for_solve_exact_for_mus():
    """決定事項: 求解は CP-SAT、MUS は exact。既定値がそれで、診断も exact で MUS を求める"""
    import inspect
    assert m.SOLVE_SOLVER == "ortools" and m.MUS_SOLVER == "exact"
    assert inspect.signature(m.solve).parameters["solver"].default == m.SOLVE_SOLVER
    assert inspect.signature(m.diagnose).parameters["mus_solver"].default == m.MUS_SOLVER
    d = m.diagnose(_inst_offcount(), m.Eps(), time_limit=60)
    assert d.status == m.CONFLICT and d.mus_solver == "exact"


def test_missing_mus_solver_fails_loudly_instead_of_switching():
    """MUS ソルバーが使えないとき、黙って別のソルバーに替えず、導入方法つきのエラーにする"""
    with pytest.raises(RuntimeError, match="pip install exact"):
        m.diagnose(_inst_offcount(), m.Eps(), time_limit=60, mus_solver="no_such_solver")
