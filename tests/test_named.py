"""制約の名前（ID・制約クラス・メッセージ）。仕様は docs/design/constraint-message-spec.md。"""
import dataclasses as dc
import datetime
import re
import sys, pathlib
import pytest
from cpmpy.transformations.get_variables import get_variables
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import nurse_csp as m
from nurse.registry import Aux, Item

START = datetime.date(2026, 11, 2)                   # 月曜日（start_weekday=0 と一致）
ID_FORMAT = re.compile(r"^[0-9A-Za-z_]+-\d{4,}$")


def _all_features() -> dict[str, list[m.Named]]:
    """全機能を有効にした名前つき制約（休日の暦日判定・休みの下限・個別要望・整列を含む）"""
    I = m.with_requests(m.synthetic(T=31, start_date=START))
    I = dc.replace(I, rules=dc.replace(I.rules, guideline=dc.replace(I.rules.guideline, min_off_block=2),
                                       law=dc.replace(I.rules.law, holiday_strict_calendar=True)))
    return m.NurseModel(I, m.Eps(symmetry_break="relaxed")).build_named()


def test_build_and_build_named_agree() -> None:
    I, eps = m.synthetic(), m.Eps()
    named = m.NurseModel(I, eps).build_named()
    plain = m.NurseModel(I, eps).build()
    assert {k: [str(x.expr) for x in v] for k, v in named.items()} == {k: [str(e) for e in v] for k, v in plain.items()}
    assert list(named) == list(plain)


def test_every_group_has_a_short_unique_class_name() -> None:
    titles = [spec.title for spec in m.REGISTRY.values()]
    assert all(titles) and len(titles) == len(set(titles))
    assert all(len(t) <= 12 for t in titles), [t for t in titles if len(t) > 12]


def test_ids_are_unique_and_sequential_per_group() -> None:
    named = _all_features()
    ids = [x.id for items in named.values() for x in items]
    assert len(ids) == len(set(ids))                                           # 全制約でモデル全体で一意
    for group, items in named.items():
        assert [x.id for x in items] == [f"{group}-{i + 1:04d}" for i in range(len(items))]   # 群内で 0001 から連続
        assert all(ID_FORMAT.match(x.id) and x.group == group and x.title == m.REGISTRY[group].title for x in items)


def test_aux_and_background_have_ids_but_no_message() -> None:
    named = _all_features()
    for items in named.values():
        for x in items:
            if x.hard:
                assert x.id and x.message == ""


def test_every_explainable_constraint_has_a_message() -> None:
    """診断に出る制約（hard でない = soft と前提）は、すべてメッセージを持つ（空でなく、空白の連続もない）"""
    for group, items in _all_features().items():
        for x in items:
            if not x.hard:
                assert x.message and "  " not in x.message and x.message == x.message.strip(), (x.id, x.message)


def test_hard_means_not_relaxable_or_aux() -> None:
    named = m.NurseModel(m.synthetic(), m.Eps()).build_named()
    assert all(x.hard for x in named["struct"] + named["history"])
    assert any(x.hard for x in named["72h"]) and any(not x.hard for x in named["72h"])     # 補助の定義は hard、本体は soft
    assert all(not x.hard for x in named["cover"] + named["holiday"] + named["offcount"])
    assert sum(not x.hard for x in named["72h"]) == 1


def test_message_is_generated_lazily_not_at_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """メッセージは診断のときだけ作る: build() は整形器を使わず、Named.message にアクセスしたときに初めて整形する"""
    calls = []

    class Spy(m.Renderer):
        def message(self, target: tuple, params: tuple) -> str:
            calls.append(1)
            return super().message(target, params)

    monkeypatch.setattr("nurse.registry.Renderer", Spy)
    M = m.NurseModel(m.synthetic(), m.Eps())
    named = M.build_named()
    M.build()
    assert calls == []
    assert next(x for x in named["offcount"]).message == "N00 公休数=9" and calls == [1]


def test_plain_expression_and_item_and_aux_are_normalized() -> None:
    M = m.NurseModel(m.synthetic(), m.Eps())
    spec = m.REGISTRY["rest"]
    plain, item, aux = M._named(spec, 4, "expr"), M._named(spec, 0, Item("e", ("病棟",), (m.Param("値", 1),))), M._named(spec, 1, Aux("e"))
    assert plain.id == "rest-0005" and plain.message == "" and not plain.hard
    assert item.id == "rest-0001" and item.message == "病棟 値=1"
    assert aux.id == "rest-0002" and aux.hard and aux.message == ""


def test_line_format_is_id_then_class_then_message() -> None:
    named = m.NurseModel(m.synthetic(start_date=START), m.Eps()).build_named()
    assert named["cover"][0].line == "cover-0001  [人数制約下限] 11/02 00:00 - 11/02 00:30 勤務者数=3"


def test_messages_of_every_group_with_start_date() -> None:
    """仕様 8 節の一覧（開始日あり）。区別の付く例を、群ごとに固定する"""
    named = m.NurseModel(m.synthetic(start_date=START), m.Eps()).build_named()
    first = lambda g: next(x for x in named[g] if not x.hard).line
    assert first("holiday") == "holiday-0001  [休日数下限] N00 11/02 - 11/08 休日数=1"
    assert first("offcount") == "offcount-0001  [公休数一致] N00 公休数=9"
    assert first("cover") == "cover-0001  [人数制約下限] 11/02 00:00 - 11/02 00:30 勤務者数=3"
    assert first("skill") == "skill-0001  [リーダー数下限] 11/02 準夜 リーダー数=1"
    assert named["72h"][-1].line == "72h-0249  [夜勤時間平均上限] 病棟 月平均夜勤時間=72h"
    assert first("rest") == "rest-0001  [勤務間隔下限] N00 11/01 - 11/02 勤務間隔=11h 前日終業>=14:30 翌日始業<01:30"
    assert first("consec") == "consec-0001  [連続勤務日数上限] N00 10/28 - 11/02 連続勤務日数=5"
    assert first("night_band") == "night_band-0001  [夜勤回数範囲] N00 夜勤回数下限=5"
    assert named["night_band"][1].message == "N00 夜勤回数上限=8"
    assert first("weekend_spread") == "weekend_spread-0002  [土日勤務日数差上限] 正規 N00 土日勤務日数差=2"
    assert first("req_off") == "req_off-0001  [休み要望違反数上限] 要望数=60 違反数=18"     # 希望休（各人2日）も休み要望。上限は ⌊0.3×60⌋


def test_spec_table_matches_the_implementation() -> None:
    """仕様書 8 節の表（群・クラス名・メッセージの例）を読み取り、全機能を有効にした実装の出力と照合する"""
    spec = (pathlib.Path(__file__).resolve().parents[1] / "docs/design/constraint-message-spec.md").read_text(encoding="utf-8")
    table = spec.split("## 8.", 1)[1].split("## 9.", 1)[0]
    rows = re.findall(r"^\| `([\w]+)` \| (\S+) \| (.+) \|$", table, flags=re.M)
    named = _all_features()
    assert [g for g, _, _ in rows] == list(named)                            # 群の並びも一致
    for g, title, example in rows:
        soft = [x for x in named[g] if not x.hard]
        assert named[g][0].title == title, g
        if not soft:
            assert example.startswith("（"), g                               # 背景の群はメッセージなし
            continue
        got = soft[-1:] if g == "72h" else soft[:2] if g == "night_band" else soft[:1]
        assert re.findall(r"`([^`]+)`", example) == [x.message for x in got], g


def test_messages_of_remaining_groups() -> None:
    """全機能を有効にしたときにだけ現れる群（休み連続日数・勤務週末・整列・要望）のメッセージ"""
    named = _all_features()
    first = lambda g: next(x for x in named[g] if not x.hard).message
    assert first("offblock") == "N00 11/02 休み連続日数=2 ブロック長=1"
    assert first("weekend") == "N00 勤務週末数=3"
    assert first("sym_break") == "N00 夜勤回数>=N01"
    assert re.fullmatch(r"要望数=\d+ 違反数=\d+", first("req_off")) and re.fullmatch(r"要望数=\d+ 違反数=\d+", first("req_shift"))
    assert first("fixed").endswith(" 勤務=休") and first("fixed").startswith("N00 ")


def test_cover_message_shows_the_base_interval_and_ends_at_next_day_midnight() -> None:
    """人数制約のメッセージは、実際に効いている基本区間（人数を数える小区間）と勤務者数だけで表す。
    需要区間は出さない。統合しないので、文面は制約ごとに異なる。17:30〜24:00 は翌日の 00:00 で終わる"""
    named = m.NurseModel(m.synthetic(start_date=START), m.Eps()).build_named()
    day0 = [x.message for x in named["cover"] if x.message.startswith("11/02")]
    assert "11/02 12:30 - 11/02 13:00 勤務者数=10" in day0
    assert "11/02 17:30 - 11/03 00:00 勤務者数=3" in day0
    assert not any(s.startswith("11/02 08:30 - 11/02 17:00") for s in day0)     # 需要区間そのものは出ない
    assert len(day0) == len(set(day0))                                            # 基本区間ごとなので文面も重ならない


def test_rest_messages_differ_per_threshold() -> None:
    named = m.NurseModel(m.synthetic(), m.Eps()).build_named()
    first = [x.message for x in named["rest"] if x.message.startswith("N00 前月末1日前")]
    assert len(first) == 2 and first[0] != first[1] and all("勤務間隔=11h" in s for s in first)


def test_messages_without_start_date_fall_back_to_day_names() -> None:
    named = m.NurseModel(m.synthetic(), m.Eps()).build_named()
    assert named["cover"][0].line == "cover-0001  [人数制約下限] 1日目(月) 00:00 - 1日目(月) 00:30 勤務者数=3"
    assert next(x for x in named["consec"]).message == "N00 前月末5日前 - 1日目(月) 連続勤務日数=5"


def test_nurse_and_category_names_come_from_the_instance() -> None:
    I = m.synthetic(start_date=START)
    I = dc.replace(I, nurse_names=[f"職員{n:03d}" for n in range(I.n_nurses)], cat_names={"E": "夕勤"})
    named = m.NurseModel(I, m.Eps()).build_named()
    assert next(x for x in named["offcount"]).message == "職員000 公休数=9"
    assert next(x for x in named["skill"]).message == "11/02 夕勤 リーダー数=1"


def test_off_wishes_and_off_requests_are_one_kind_of_request() -> None:
    """希望休と休み要望は、勤務者からの同じ種類の要望として `req_off` に一本化している（上限は割合 ρ だけで決まる）。
    with_requests は、元の要望を残したまま追加する。"""
    I = m.synthetic()
    assert sum(map(len, I.req_off.values())) == 2 * I.n_nurses and not I.req_shift      # 基本の要望: 各人2日
    assert not hasattr(m.Eps(), "soft_off_viol") and not hasattr(I, "soft_off")           # 件数指定（κ）は廃止
    J = m.with_requests(I, rate=0.1)
    for n, ds in I.req_off.items():
        assert set(ds) <= set(J.req_off[n])                                              # 元の要望は残る
    added = sum(map(len, J.req_off.values())) - sum(map(len, I.req_off.values()))
    assert added == round(0.1 * I.n_nurses * I.n_days) // 2
    cells = [(n, d) for n, ds in J.req_off.items() for d in ds] + [(n, d) for n, ds in J.req_shift.items() for d, _ in ds]
    assert len(cells) == len(set(cells)) and not any(c in J.fixed for c in cells)        # 同じ日に重複せず、確定割当とも重ならない
    K = m.with_requests(J, rate=0.1, seed=1)                                             # 続けて足すと、さらに加わる
    assert sum(map(len, K.req_off.values())) > sum(map(len, J.req_off.values()))


def test_build_named_reuses_the_result_for_the_same_enabled_set() -> None:
    """同じ有効な制約の集合なら、build_named は最初の結果（Named と式）を再利用する。求解（build）と診断が同じ式を共有し、
    補助変数も作り直さない。有効な集合が違えば別に作る。"""
    M = m.NurseModel(m.synthetic(), m.Eps())
    a, b = M.build_named(), M.build_named()
    assert a is not b and all(a[g] is not b[g] for g in a)                          # 辞書・リストは呼び出しごとの新しい入れ物
    assert all(x is y for g in a for x, y in zip(a[g], b[g]))                       # 中の Named（と式）は共有
    plain = M.build()
    assert all(p is x.expr for g in a for p, x in zip(plain[g], a[g]))              # build() も同じ式
    aux = lambda named: {id(v) for g in named for x in named[g] if x.hard for v in get_variables(x.expr)}
    assert aux(a) == aux(M.build_named())                                           # 補助変数も同じ（作り直さない）
    mandatory = {nm for nm, sp in m.REGISTRY.items() if sp.scope in m.MANDATORY}
    sub = M.build_named(mandatory)                                                  # 有効な集合が違えば、別に作る
    assert set(sub) == mandatory and sub["struct"][0] is not a["struct"][0]
    assert M.build_named(mandatory)["struct"][0] is sub["struct"][0]                # 同じ集合なら、また再利用
    full = M.build_named()
    assert full["cover"][0] is a["cover"][0] and M.is_active("cover")               # 元の結果も残り、有効な集合の記録も戻る
