"""制約の名前（ID・制約クラス・メッセージ）。仕様は docs/design/constraint-message-spec.md。"""
import dataclasses as dc
import datetime
import re
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import nurse_csp as m
from nurse.registry import Aux, Item

START = datetime.date(2026, 11, 2)                   # 月曜日（start_weekday=0 と一致）
ID_FORMAT = re.compile(r"^[0-9A-Za-z_]+-\d{4,}$")


def _all_features():
    """全機能を有効にした名前つき制約（休日の暦日判定・休みの下限・個別要望・整列を含む）"""
    I = m.with_requests(m.synthetic(T=31, start_date=START))
    I = dc.replace(I, rules=dc.replace(I.rules, guideline=dc.replace(I.rules.guideline, min_off_block=2),
                                       law=dc.replace(I.rules.law, holiday_strict_calendar=True)))
    return m.NurseModel(I, m.Eps(symmetry_break="relaxed")).build_named()


def test_build_and_build_named_agree():
    I, eps = m.synthetic(), m.Eps()
    named = m.NurseModel(I, eps).build_named()
    plain = m.NurseModel(I, eps).build()
    assert {k: [str(x.expr) for x in v] for k, v in named.items()} == {k: [str(e) for e in v] for k, v in plain.items()}
    assert list(named) == list(plain)


def test_every_group_has_a_short_unique_class_name():
    titles = [spec.title for spec in m.REGISTRY.values()]
    assert all(titles) and len(titles) == len(set(titles))
    assert all(len(t) <= 12 for t in titles), [t for t in titles if len(t) > 12]


def test_ids_are_unique_and_sequential_per_group():
    named = _all_features()
    ids = [x.id for items in named.values() for x in items]
    assert len(ids) == len(set(ids))                                           # 全制約でモデル全体で一意
    for group, items in named.items():
        assert [x.id for x in items] == [f"{group}-{i + 1:04d}" for i in range(len(items))]   # 群内で 0001 から連続
        assert all(ID_FORMAT.match(x.id) and x.group == group and x.title == m.REGISTRY[group].title for x in items)


def test_aux_and_background_have_ids_but_no_message():
    named = _all_features()
    for items in named.values():
        for x in items:
            if x.hard:
                assert x.id and x.message == ""


def test_every_explainable_constraint_has_a_message():
    """診断に出る制約（hard でない = soft と前提）は、すべてメッセージを持つ（空でなく、空白の連続もない）"""
    for group, items in _all_features().items():
        for x in items:
            if not x.hard:
                assert x.message and "  " not in x.message and x.message == x.message.strip(), (x.id, x.message)


def test_hard_means_not_relaxable_or_aux():
    named = m.NurseModel(m.synthetic(), m.Eps()).build_named()
    assert all(x.hard for x in named["struct"] + named["history"])
    assert any(x.hard for x in named["72h"]) and any(not x.hard for x in named["72h"])     # 補助の定義は hard、本体は soft
    assert all(not x.hard for x in named["cover"] + named["holiday"] + named["offcount"])
    assert sum(not x.hard for x in named["72h"]) == 1


def test_message_is_generated_lazily_not_at_build(monkeypatch):
    """メッセージは診断のときだけ作る: build() は整形器を使わず、Named.message にアクセスしたときに初めて整形する"""
    calls = []

    class Spy(m.Renderer):
        def message(self, target, params):
            calls.append(1)
            return super().message(target, params)

    monkeypatch.setattr("nurse.registry.Renderer", Spy)
    M = m.NurseModel(m.synthetic(), m.Eps())
    named = M.build_named()
    M.build()
    assert calls == []
    assert next(x for x in named["offcount"]).message == "N00 公休数=9" and calls == [1]


def test_plain_expression_and_item_and_aux_are_normalized():
    M = m.NurseModel(m.synthetic(), m.Eps())
    spec = m.REGISTRY["rest"]
    plain, item, aux = M._named(spec, 4, "expr"), M._named(spec, 0, Item("e", ("病棟",), (m.Param("値", 1),))), M._named(spec, 1, Aux("e"))
    assert plain.id == "rest-0005" and plain.message == "" and not plain.hard
    assert item.id == "rest-0001" and item.message == "病棟 値=1"
    assert aux.id == "rest-0002" and aux.hard and aux.message == ""


def test_line_format_is_id_then_class_then_message():
    named = m.NurseModel(m.synthetic(start_date=START), m.Eps()).build_named()
    assert named["cover"][0].line == "cover-0001  [人数制約下限] 11/02 00:00 - 11/02 08:30 勤務者数=3"


def test_messages_of_every_group_with_start_date():
    """仕様 8 節の一覧（開始日あり）。区別の付く例を、群ごとに固定する"""
    named = m.NurseModel(m.synthetic(start_date=START), m.Eps()).build_named()
    first = lambda g: next(x for x in named[g] if not x.hard).line
    assert first("holiday") == "holiday-0001  [休日数下限] N00 11/02 - 11/08 休日数=1"
    assert first("offcount") == "offcount-0001  [公休数一致] N00 公休数=9"
    assert first("cover") == "cover-0001  [人数制約下限] 11/02 00:00 - 11/02 08:30 勤務者数=3"
    assert first("skill") == "skill-0001  [リーダー数下限] 11/02 準夜 リーダー数=1"
    assert named["72h"][-1].line == "72h-0249  [夜勤時間平均上限] 病棟 月平均夜勤時間=72h"
    assert first("rest") == "rest-0001  [勤務間隔下限] N00 11/01 - 11/02 勤務間隔=11h 前日終業>=14:30 翌日始業<01:30"
    assert first("consec") == "consec-0001  [連続勤務日数上限] N00 10/28 - 11/02 連続勤務日数=5"
    assert first("night_band") == "night_band-0001  [夜勤回数範囲] N00 夜勤回数下限=5"
    assert named["night_band"][1].message == "N00 夜勤回数上限=8"
    assert first("weekend_spread") == "weekend_spread-0002  [土日勤務日数差上限] 正規 N00 土日勤務日数差=2"
    assert first("soft_off") == "soft_off-0001  [希望休違反数上限] 要望数=60 違反数=10"


def test_messages_of_remaining_groups():
    """全機能を有効にしたときにだけ現れる群（休み連続日数・勤務週末・整列・要望）のメッセージ"""
    named = _all_features()
    first = lambda g: next(x for x in named[g] if not x.hard).message
    assert first("offblock") == "N00 11/02 休み連続日数=2 ブロック長=1"
    assert first("weekend") == "N00 勤務週末数=3"
    assert first("sym_break") == "N00 夜勤回数>=N01"
    assert re.fullmatch(r"要望数=\d+ 違反数=\d+", first("req_off")) and re.fullmatch(r"要望数=\d+ 違反数=\d+", first("req_shift"))
    assert first("fixed").endswith(" 勤務=休") and first("fixed").startswith("N00 ")


def test_cover_message_uses_the_demand_interval_and_ends_at_next_day_midnight():
    """人数制約のメッセージは需要区間（人数を指定する単位）で表す。17:00〜24:00 は翌日の 00:00 で終わる"""
    named = m.NurseModel(m.synthetic(start_date=START), m.Eps()).build_named()
    spans = {x.message.rsplit(" 勤務者数", 1)[0] for x in named["cover"] if x.message.startswith("11/02")}
    assert spans == {"11/02 00:00 - 11/02 08:30", "11/02 08:30 - 11/02 17:00", "11/02 17:00 - 11/03 00:00"}
    assert len(named["cover"]) > 3 * 30                                          # 制約は基本区間ごとで、メッセージは同じになる（ID で区別）


def test_rest_messages_differ_per_threshold():
    named = m.NurseModel(m.synthetic(), m.Eps()).build_named()
    first = [x.message for x in named["rest"] if x.message.startswith("N00 前月末1日前")]
    assert len(first) == 2 and first[0] != first[1] and all("勤務間隔=11h" in s for s in first)


def test_messages_without_start_date_fall_back_to_day_names():
    named = m.NurseModel(m.synthetic(), m.Eps()).build_named()
    assert named["cover"][0].line == "cover-0001  [人数制約下限] 1日目(月) 00:00 - 1日目(月) 08:30 勤務者数=3"
    assert next(x for x in named["consec"]).message == "N00 前月末5日前 - 1日目(月) 連続勤務日数=5"


def test_nurse_and_category_names_come_from_the_instance():
    I = m.synthetic(start_date=START)
    I = dc.replace(I, nurse_names=[f"職員{n:03d}" for n in range(I.n_nurses)], cat_names={"E": "夕勤"})
    named = m.NurseModel(I, m.Eps()).build_named()
    assert next(x for x in named["offcount"]).message == "職員000 公休数=9"
    assert next(x for x in named["skill"]).message == "11/02 夕勤 リーダー数=1"
