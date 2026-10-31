"""制約式の名前づけ: build() は式だけ、build_named() は群・日本語ラベル・hard を持つ。両者は同じ式を返す。"""
import dataclasses as dc
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import nurse_csp as m
from nurse.registry import Aux, Labeled


def test_build_and_build_named_agree():
    I, eps = m.synthetic(), m.Eps()
    M = m.NurseModel(I, eps)
    named = M.build_named()
    flat = {k: [x.expr for x in v] for k, v in named.items()}
    plain = m.NurseModel(I, eps).build()
    assert {k: [str(e) for e in v] for k, v in flat.items()} == {k: [str(e) for e in v] for k, v in plain.items()}
    assert list(named) == list(plain)


def test_every_group_has_a_title_and_every_soft_constraint_a_label():
    I = m.with_requests(m.synthetic(T=31))
    I = dc.replace(I, rules=dc.replace(I.rules, guideline=dc.replace(I.rules.guideline, min_off_block=2),
                                       law=dc.replace(I.rules.law, holiday_strict_calendar=True)))
    named = m.NurseModel(I, m.Eps(symmetry_break="relaxed")).build_named()
    for name, spec in m.REGISTRY.items():
        assert spec.title, f"{name}: title が無い"
    for name, items in named.items():
        for x in items:
            assert x.group == name
            if not x.hard:
                assert x.label and "#" not in x.label, (name, x.label)      # 個別のラベルがある（自動の番号名ではない）


def test_hard_means_not_relaxable_or_aux():
    named = m.NurseModel(m.synthetic(), m.Eps()).build_named()
    assert all(x.hard for x in named["struct"] + named["history"])
    assert any(x.hard for x in named["72h"]) and any(not x.hard for x in named["72h"])     # 補助の定義は hard、本体は soft
    assert all(not x.hard for x in named["cover"] + named["holiday"] + named["offcount"])
    assert sum(not x.hard for x in named["72h"]) == 1


def test_unlabeled_expression_gets_an_automatic_name():
    spec = m.REGISTRY["rest"]
    assert m.NurseModel._named(spec, 4, "expr").label == "勤務間隔の下限 #5"
    assert m.NurseModel._named(spec, 0, Labeled("e", "説明")).label == "説明"
    a = m.NurseModel._named(spec, 0, Aux("e"))
    assert a.hard and a.label == ""


def test_labels_read_naturally():
    I = m.synthetic(T=31)
    named = m.NurseModel(I, m.Eps()).build_named()
    assert named["offcount"][3].label == "N03: 公休をちょうど9日"
    assert named["holiday"][0].label.startswith("N00: ") and "休日が1日以上" in named["holiday"][0].label
    assert named["skill"][0].label == "1日目(月): 準夜に夜勤リーダー資格者が1人以上"
    assert any(x.label == "病棟の月平均夜勤時間が72時間以下（算入者の平均。診療報酬の施設基準）" for x in named["72h"])
    assert m.day_name(I, 5) == "6日目(土)" and m.time_name(I, 0) == "前月末5日前" and m.slot_name(17) == "8:30"
