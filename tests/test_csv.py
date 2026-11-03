"""CSV の入出力（nurse_csv.py）: 見本の作成 → 読み込みで合成インスタンスと一致すること、入力の誤りの検出、実行結果の CSV。
求解を含むテストは、見本（30人・30日）を使う。"""
import csv
import dataclasses
import sys, pathlib
from collections.abc import Callable
import pytest
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import nurse_csv as c
from nurse import InputError, ReturnCode, synthetic


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    d = tmp_path_factory.mktemp("csv") / "in"
    c.write_sample(d)
    return d


def _copy(src: pathlib.Path, dst: pathlib.Path, edit: Callable[[str], str] | None = None, name: str | None = None) -> pathlib.Path:
    """入力ディレクトリを複製し、name のファイルだけ文字列変換 edit をかける"""
    import shutil
    shutil.copytree(src, dst)
    if name and edit:
        p = dst / name
        p.write_text(edit(p.read_text(encoding="utf-8-sig")), encoding="utf-8-sig")
    return dst


def test_sample_round_trips_to_the_synthetic_instance(sample: pathlib.Path) -> None:
    L = c.load_input(sample)
    ref = synthetic(26, 4, T=30, seed=0, start_date=L.instance.start_date)
    I = L.instance
    assert I.n_nurses == ref.n_nurses and I.n_days == ref.n_days and I.start_weekday == ref.start_weekday == 0
    assert [dataclasses.astuple(s) for s in I.shifts] == [dataclasses.astuple(s) for s in ref.shifts]
    assert I.domain == [sorted(d) for d in ref.domain] and I.off_days == ref.off_days and I.seniors == ref.seniors
    assert (I.history == ref.history).all() and I.demand == [sorted(d) for d in ref.demand]
    assert I.fixed == ref.fixed and I.req_off == ref.req_off and I.req_shift == ref.req_shift
    assert I.rules.law == ref.rules.law and I.rules.domain == ref.rules.domain and I.rules.guideline == ref.rules.guideline
    assert I.leader_cats == ref.leader_cats and I.night_cats == ref.night_cats and abs(I.night_avg - ref.night_avg) < 1e-4
    assert I.rules.off == frozenset()
    assert L.eps == type(L.eps)() and (L.solve_time_limit, L.mus_time_limit) == (30, 30)


def test_time_notation() -> None:
    assert [c.parse_time(x, "t") for x in ("00:00", "08:30", "24:00", "25:30")] == [0, 17, 48, 51]
    assert c.format_time(51) == "25:30"
    for bad in ("8:15", "ab", "08:00:00", "24:60"):
        with pytest.raises(InputError):
            c.parse_time(bad, "t")


@pytest.mark.parametrize("name, edit, message", [
    ("fixed.csv", lambda s: s.replace("看護師01", "看護師99", 1), "看護師99"),                      # 氏名が nurses.csv にない
    ("fixed.csv", lambda s: s.replace("2026-11-21", "2027-01-01", 1), "の外"),
    ("params.csv", lambda s: s.replace("n_days,30", "n_days,abc"), "整数"),
    ("params.csv", lambda s: s + "unknown_key,1,x,y\n", "未知のキー"),
    ("params.csv", lambda s: s.replace("holiday_rule,weekly", "holiday_rule,monthly"), "holiday_rule"),
    ("demand.csv", lambda s: s.replace("月,00:00,08:30,3", "月,00:00,08:15,3"), "30分単位"),
    ("demand.csv", lambda s: s.replace("月,00:00,08:30,3", "月,00:00,08:00,3"), "空きなく"),
    ("demand.csv", lambda s: "\n".join(l for l in s.splitlines() if not l.startswith("日,")) + "\n", "日曜日の需要がない"),
    ("shifts.csv", lambda s: s.replace("休,00:00", "お休み,00:00", 1), "公休の勤務"),
    ("nurses.csv", lambda s: s.replace("休|日早", "休|なし|日早", 1), "取れる勤務"),
    ("history.csv", lambda s: s.replace("準,休,深", "準,謎,深", 1), "勤務名"),
    ("constraints.csv", lambda s: s + "kernel_x,層,x,,0\n", "外せる制約にない"),
    ("requests.csv", lambda s: s.replace("休み", "お願い", 1), "種別"),
])
def test_input_errors_name_the_file_and_the_cause(sample: pathlib.Path, tmp_path: pathlib.Path, name: str, edit: Callable[[str], str], message: str) -> None:
    d = _copy(sample, tmp_path / "in", edit, name)
    with pytest.raises(InputError, match=message) as e:
        c.load_input(d)
    assert any(w in str(e.value) for w in (name, "入力データの整合"))      # どのファイルの誤りか分かる（需要の隙間などは Instance の検査なので「入力データの整合」）


def test_missing_file_is_an_input_error(sample: pathlib.Path, tmp_path: pathlib.Path) -> None:
    d = _copy(sample, tmp_path / "in")
    (d / "demand.csv").unlink()
    with pytest.raises(InputError, match="demand.csv: ファイルがない"):
        c.load_input(d)


def test_turning_a_constraint_off_reaches_the_rules(sample: pathlib.Path, tmp_path: pathlib.Path) -> None:
    d = _copy(sample, tmp_path / "in", lambda s: s.replace("cover,層2 病院・看護職共通,人数制約下限,REIMB-NIGHT-2,1", "cover,層2 病院・看護職共通,人数制約下限,REIMB-NIGHT-2,0"), "constraints.csv")
    assert c.load_input(d).instance.rules.off == {"cover"}


def _read(path: pathlib.Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def test_run_writes_the_schedule_for_a_feasible_input(sample: pathlib.Path, tmp_path: pathlib.Path) -> None:
    out = tmp_path / "out"
    assert c.run_dir(sample, out) == ReturnCode.OK
    st = {r[0]: r[1] for r in _read(out / "status.csv")[1:]}
    assert st["status"] == "feasible" and st["code"] == "0" and st["n_nurses"] == "30"
    sched = _read(out / "schedule.csv")
    assert len(sched) == 31 and len(sched[0]) == 31 and sched[0][0] == "氏名" and sched[0][1] == st["start_date"]
    shifts = {r[0] for r in _read(sample / "shifts.csv")[1:]}
    assert all(v in shifts for r in sched[1:] for v in r[1:])
    assert not (out / "conflicts.csv").exists()
    fixed = {(r[0], r[1]): r[2] for r in _read(sample / "fixed.csv")[1:]}               # 固定割当が守られている
    row = {r[0]: dict(zip(sched[0][1:], r[1:])) for r in sched[1:]}
    assert all(row[n][d] == s for (n, d), s in fixed.items())


def test_run_lists_the_conflicting_constraints(sample: pathlib.Path, tmp_path: pathlib.Path) -> None:
    d = _copy(sample, tmp_path / "in", lambda s: s.replace("月,08:30,17:00,10", "月,08:30,17:00,40"), "demand.csv")
    out = tmp_path / "out"
    assert c.run_dir(d, out) == ReturnCode.CONFLICT
    st = {r[0]: r[1] for r in _read(out / "status.csv")[1:]}
    assert st["status"] == "conflict" and st["code"] == "20" and "勤務者数=40" in st["message"]
    rows = _read(out / "conflicts.csv")
    assert rows[0] == ["区分", "ID", "制約群", "制約クラス", "層", "メッセージ"] and len(rows) > 1
    assert any(r[2] == "cover" and "勤務者数=40" in r[5] for r in rows[1:]) and all(r[0] in ("前提", "外せる") for r in rows[1:])
    assert not (out / "schedule.csv").exists()
    assert len(_read(out / "precheck.csv")) > 1               # 事前検査も需要の不足を指摘する


def test_run_reports_an_input_error_in_status(sample: pathlib.Path, tmp_path: pathlib.Path) -> None:
    d = _copy(sample, tmp_path / "in", lambda s: s.replace("n_days,30", "n_days,0"), "params.csv")
    out = tmp_path / "out"
    assert c.run_dir(d, out) == ReturnCode.INPUT_ERROR
    st = {r[0]: r[1] for r in _read(out / "status.csv")[1:]}
    assert st["status"] == "error" and st["code"] == "30" and "n_days" in st["message"]


def test_run_removes_the_old_output(sample: pathlib.Path, tmp_path: pathlib.Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "conflicts.csv").write_text("古い結果", encoding="utf-8")
    bad = _copy(sample, tmp_path / "in", lambda s: s.replace("n_days,30", "n_days,0"), "params.csv")
    c.run_dir(bad, out)
    assert not (out / "conflicts.csv").exists()


def test_cli_exit_codes(sample: pathlib.Path, tmp_path: pathlib.Path) -> None:
    bad = _copy(sample, tmp_path / "in", lambda s: s.replace("n_days,30", "n_days,0"), "params.csv")
    assert c.main(["run", str(bad), str(tmp_path / "out")]) == int(ReturnCode.INPUT_ERROR)
    assert c.main(["init", str(tmp_path / "small"), "--nurses", "2"]) == int(ReturnCode.INPUT_ERROR)
