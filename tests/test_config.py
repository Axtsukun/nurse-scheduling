"""設定ファイル（nurse.properties）とリターンコード。時間制限をコードの外で変えられること、状態と例外がコードで分かること。"""
import dataclasses as dc
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
import nurse_csp as m
from nurse import config
from nurse.codes import ReturnCode, NurseError, InputError, SolverUnavailableError, InternalError, code_of


def _props(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    p = tmp_path / "t.properties"
    p.write_text(text, encoding="utf-8")
    return p


# ---- 設定ファイル ----
def test_default_properties_file_is_30_seconds_each() -> None:
    """リポジトリ直下の nurse.properties の既定は、求解・MUS とも 30 秒"""
    assert config.DEFAULT_PATH.is_file()
    assert m.load_settings() == m.Settings(solve_time_limit=30.0, mus_time_limit=30.0)
    assert config.parse_properties("") == m.Settings(30.0, 30.0)                  # キーが無ければ既定値


def test_properties_syntax_comments_spaces_and_override() -> None:
    s = config.parse_properties("# コメント\n! これもコメント\n\n solve.time_limit = 12.5 \nmus.time_limit=7\n")
    assert s == m.Settings(12.5, 7.0)
    assert config.parse_properties("mus.time_limit=5").solve_time_limit == 30.0    # 書かなかったキーは既定値


@pytest.mark.parametrize("text,needle", [
    ("solve.time_limit\n", "キー=値"),
    ("solve.timelimit=10\n", "未知のキー"),
    ("solve.time_limit=10\nsolve.time_limit=20\n", "重複"),
    ("solve.time_limit=abc\n", "数"),
    ("mus.time_limit=0\n", "正の数"),
    ("mus.time_limit=-3\n", "正の数"),
    ("mus.time_limit=nan\n", "正の数"),
])
def test_invalid_properties_are_input_errors_with_the_line(text: str, needle: str) -> None:
    with pytest.raises(InputError, match=needle) as e:
        config.parse_properties(text, "x.properties")
    assert "x.properties:" in str(e.value) and e.value.code == ReturnCode.INPUT_ERROR


def test_load_settings_resolution_order(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _props(tmp_path, "solve.time_limit=11\nmus.time_limit=22\n")
    monkeypatch.setenv(config.ENV_VAR, str(env))
    assert m.load_settings() == m.Settings(11.0, 22.0)                              # 環境変数
    arg = tmp_path / "arg.properties"
    arg.write_text("solve.time_limit=33\n", encoding="utf-8")
    assert m.load_settings(arg) == m.Settings(33.0, 30.0)                           # 引数が環境変数より優先
    monkeypatch.setenv(config.ENV_VAR, str(tmp_path / "nothing.properties"))
    with pytest.raises(InputError, match="見つからない"):                          # 指定したファイルが無ければ、黙って既定値にしない
        m.load_settings()
    with pytest.raises(InputError, match="見つからない"):
        m.load_settings(tmp_path / "nothing.properties")


def test_time_limits_in_the_properties_file_are_used_when_arguments_are_omitted(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """solve() / diagnose() は、時間制限の引数を省略すると properties の値を使う（コードの変更は不要）"""
    monkeypatch.setenv(config.ENV_VAR, str(_props(tmp_path, "solve.time_limit=0.05\n")))
    d = m.diagnose(m.synthetic(52, 8, T=31), m.Eps())                               # 引数なし
    assert d.status == m.TIMEOUT and d.code == ReturnCode.SOLVE_TIME_LIMIT
    # 引数で渡せば、引数が優先する
    assert m.solve(m.synthetic(), m.Eps(), time_limit=30).ok


def test_mus_time_limit_in_the_properties_file(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    I = m.synthetic()
    I = dc.replace(I, rules=dc.replace(I.rules, domain=dc.replace(I.rules.domain, limit_units=40)))   # MUS が終わらない矛盾
    monkeypatch.setenv(config.ENV_VAR, str(_props(tmp_path, "solve.time_limit=60\nmus.time_limit=0.001\n")))
    d = m.diagnose(I, m.Eps())
    assert d.code == ReturnCode.CONFLICT_UNIDENTIFIED


# ---- リターンコード ----
def test_return_codes_are_distinct_documented_and_grouped() -> None:
    codes = list(ReturnCode)
    assert len({int(c) for c in codes}) == len(codes) and ReturnCode.OK == 0
    assert all(c.label for c in codes)
    assert [c.is_error for c in codes] == [c >= 30 for c in codes]
    # 時間打ち切りと制約矛盾は、想定される結果であって異常ではない
    assert not any(c.is_error for c in (ReturnCode.OK, ReturnCode.SOLVE_TIME_LIMIT, ReturnCode.CONFLICT,
                                       ReturnCode.CONFLICT_MUS_TIME_LIMIT, ReturnCode.CONFLICT_UNIDENTIFIED))


def test_diagnosis_code_covers_every_state() -> None:
    D, x = m.Diagnosis, ["制約"]
    assert D(m.FEASIBLE).code == ReturnCode.OK
    assert D(m.TIMEOUT).code == ReturnCode.SOLVE_TIME_LIMIT
    assert D(m.CONFLICT, items=x).code == ReturnCode.CONFLICT
    assert D(m.CONFLICT, items=x, minimal=False).code == ReturnCode.CONFLICT_MUS_TIME_LIMIT
    assert D(m.CONFLICT, items=[], minimal=False).code == ReturnCode.CONFLICT_UNIDENTIFIED


def test_exceptions_map_to_return_codes_and_keep_their_builtin_type() -> None:
    assert code_of(InputError("x")) == ReturnCode.INPUT_ERROR and issubclass(InputError, ValueError)
    assert code_of(SolverUnavailableError("x")) == ReturnCode.SOLVER_UNAVAILABLE and issubclass(SolverUnavailableError, RuntimeError)
    assert code_of(InternalError("x")) == ReturnCode.INTERNAL_ERROR and issubclass(InternalError, RuntimeError)
    assert code_of(KeyError("想定外")) == ReturnCode.INTERNAL_ERROR                  # 想定外の例外は内部エラー
    assert all(issubclass(e, NurseError) for e in (InputError, SolverUnavailableError, InternalError))


def test_invalid_input_raises_input_error() -> None:
    I = m.synthetic()
    with pytest.raises(InputError):                                                   # 需要が1日を覆わない
        dc.replace(I, demand=[[(0, 10, 3)]] * I.n_days)
    with pytest.raises(InputError):                                                   # 外せない制約をオフにする
        m.selected_constraints(dc.replace(I, rules=dc.replace(I.rules, off=frozenset({"holiday"}))))
    with pytest.raises(InputError):                                                   # 曜日の不一致
        m.synthetic(start_date=__import__("datetime").date(2026, 11, 3))


# ---- CLI の終了コード ----
def test_cli_exit_codes(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert m.main(["--time-limit", "30"]) == ReturnCode.OK                            # 解あり
    out = capsys.readouterr().out
    assert "終了コード: 0 OK" in out
    bad = _props(tmp_path, "solve.time_limit=oops\n")
    assert m.main(["--properties", str(bad)]) == ReturnCode.INPUT_ERROR               # 設定の誤り
    assert "終了コード: 30 INPUT_ERROR" in capsys.readouterr().out
    assert m.main(["--properties", str(tmp_path / "none")]) == ReturnCode.INPUT_ERROR  # ファイルなし
    with pytest.raises(SystemExit) as e:                                              # 引数の誤りは argparse の 2
        m.main(["--no-such-option"])
    assert e.value.code == 2
