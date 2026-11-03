"""メッセージの整形器（Renderer）と、表示方針の入力（Instance の start_date・nurse_names・cat_names）。ソルバー不要。"""
import dataclasses as dc
import datetime
from typing import Any
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
import nurse_csp as m

START = datetime.date(2026, 11, 2)                   # 月曜日


def R(**kw: Any) -> m.Renderer:
    return m.Renderer(dc.replace(m.synthetic(start_date=START), **kw))


def test_date_with_and_without_start_date() -> None:
    assert R().date(0) == "11/02" and R().date(5) == "11/07" and R().date(-5) == "10/28"
    plain = m.Renderer(m.synthetic())
    assert plain.date(0) == "1日目(月)" and plain.date(5) == "6日目(土)" and plain.date(-2) == "前月末2日前"


def test_date_crosses_month_and_year_boundaries() -> None:
    I = dc.replace(m.synthetic(), start_weekday=2, start_date=datetime.date(2026, 12, 30))      # 水曜日
    r = m.Renderer(I)
    assert [r.date(d) for d in (0, 1, 2, 3)] == ["12/30", "12/31", "01/01", "01/02"]


def test_clock_is_zero_padded_and_marks_next_day() -> None:
    r = R()
    assert [r.clock(s) for s in (0, 17, 47, 48, 50)] == ["00:00", "08:30", "23:30", "翌00:00", "翌01:00"]


def test_span_end_at_24_is_next_day_midnight() -> None:
    r = R()
    assert r.part(m.Span(17, 34)) == "11/02 08:30 - 11/02 17:00"
    assert r.part(m.Span(34, 48)) == "11/02 17:00 - 11/03 00:00"                  # 24:00 とは書かない
    assert r.part(m.Span(48 + 34, 96)) == "11/03 17:00 - 11/04 00:00"
    assert m.Renderer(m.synthetic()).part(m.Span(34, 48)) == "1日目(月) 17:00 - 2日目(火) 00:00"


def test_span_in_the_history_period() -> None:
    assert R().part(m.Span(-48 + 40, -48 + 48)) == "11/01 20:00 - 11/02 00:00"


def test_day_span_and_parts() -> None:
    r = R()
    assert r.part(m.DaySpan(1, 7)) == "11/03 - 11/09"
    assert r.part("病棟") == "病棟" and r.part(m.Day(0)) == "11/02"
    I = m.synthetic()
    assert r.part(m.Shift(I.idx("日"))) == "日"
    with pytest.raises(TypeError):
        r.part(3.14)


def test_nurse_and_category_names() -> None:
    assert R().part(m.Nurse(5)) == "N05" and R().part(m.Cat("N")) == "深夜" and R().part(m.Cat("X")) == "X"
    r = R(nurse_names=[f"山田{n}" for n in range(30)], cat_names={"D": "早番"})
    assert r.part(m.Nurse(5)) == "山田5" and r.part(m.Cat("D")) == "早番" and r.part(m.Cat("N")) == "深夜"


def test_param_formatting() -> None:
    r = R()
    assert r.param(m.Param("勤務者数", 3)) == "勤務者数=3"
    assert r.param(m.Param("勤務間隔", 11.0, "h")) == "勤務間隔=11h" and r.param(m.Param("月平均夜勤時間", 72.5, "h")) == "月平均夜勤時間=72.5h"
    assert r.param(m.Param("前日終業", m.Clock(50), op=">=")) == "前日終業>=翌01:00"
    assert r.param(m.Param("夜勤回数", m.Nurse(1), op=">=")) == "夜勤回数>=N01"


def test_message_joins_target_and_params() -> None:
    r = R()
    assert r.message((m.Nurse(0), m.Day(1)), (m.Param("勤務", m.Shift(0)), m.Param("回数", 2))) == "N00 11/03 勤務=休 回数=2"
    assert r.message((), (m.Param("要望数", 4),)) == "要望数=4" and r.message(("病棟",), ()) == "病棟"


def test_start_date_must_match_start_weekday() -> None:
    I = m.synthetic()
    assert dc.replace(I, start_date=START).start_date == START                      # 月曜日 = start_weekday 0
    with pytest.raises(ValueError, match="start_weekday"):
        dc.replace(I, start_date=datetime.date(2026, 11, 3))                        # 火曜日
    with pytest.raises(ValueError, match="start_weekday"):
        dc.replace(m.synthetic(start_date=START), start_weekday=3)


def test_nurse_names_must_match_the_number_of_nurses() -> None:
    I = m.synthetic()
    with pytest.raises(ValueError, match="nurse_names"):
        dc.replace(I, nurse_names=["a", "b"])
    assert dc.replace(I, nurse_names=[str(n) for n in range(I.n_nurses)]).nurse_names[3] == "3"
