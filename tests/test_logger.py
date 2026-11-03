"""ログ（nurse/logger.py）: 設定（YAML / JSON）の検証、親 `nurse`（①プログラム全体）と子 `nurse.engine`（②エンジン求解詳細）の
出力先（標準出力・ファイル）、必須項目（タイムスタンプ・メソッド・メッセージ）、ローテーション、各処理が残すログの内容。"""
import dataclasses as dc
import datetime
import json
import logging
import logging.handlers
import re
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
import nurse_csp as m
from nurse import logger as lg
from nurse.codes import ReturnCode

ROOT = pathlib.Path(__file__).resolve().parents[1]
LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \[[A-Z]+\] [\w.]+\.[\w\-]+: .+$")   # タイムスタンプ・メソッド・メッセージ


def _config(tmp_path: pathlib.Path, program: str = "level: INFO", engine: str = "level: INFO", extra: str = "") -> pathlib.Path:
    """テスト用のログ設定ファイル（YAML）を作る。

    Args:
        tmp_path: 一時ディレクトリ（ログの出力先もここ）。
        program: `program:` の中身（インデントなしの `キー: 値` を `, ` でつなぐ）。
        engine: `engine:` の中身。
        extra: トップレベルに足す行。

    Returns:
        設定ファイルのパス。"""
    path = tmp_path / "log.yaml"
    path.write_text(f"log_dir: {tmp_path / 'logs'}\n{extra}\nprogram: {{{program}}}\nengine: {{{engine}}}\n", encoding="utf-8")
    return path


def _read(tmp_path: pathlib.Path, name: str) -> str:
    """出力先のログファイルを読む。

    Args:
        tmp_path: 一時ディレクトリ。
        name: ログファイル名。

    Returns:
        ファイルの中身。"""
    return (tmp_path / "logs" / name).read_text(encoding="utf-8")


def test_yaml_and_json_configs_are_equivalent(tmp_path: pathlib.Path) -> None:
    data = {"log_dir": "x", "rotation": {"max_bytes": 100, "backup_count": 2},
            "program": {"level": "debug", "file": "a.log", "console": False}, "engine": {"propagate": False}}
    y, j = tmp_path / "c.yaml", tmp_path / "c.json"
    y.write_text("log_dir: x\nrotation: {max_bytes: 100, backup_count: 2}\nprogram: {level: debug, file: a.log, console: false}\n"
                 "engine: {propagate: false}\n", encoding="utf-8")
    j.write_text(json.dumps(data), encoding="utf-8")
    cfg = lg.read_logging_config(y)
    assert cfg == lg.read_logging_config(j) == lg.parse_logging_config(data)
    assert cfg.program.level == "DEBUG" and not cfg.program.console and not cfg.engine.propagate and cfg.engine.file == "nurse_engine.log"
    assert lg.parse_logging_config({}) == lg.LoggingConfig() == lg.parse_logging_config(None)         # 省略は既定値
    assert lg.LoggingConfig().engine.propagate and not lg.LoggingConfig().program.propagate         # ②は既定で①にも流す


@pytest.mark.parametrize("data,needle", [
    ({"nope": 1}, "未知のキー"),
    ({"program": {"level": "LOUD"}}, "program.level"),
    ({"program": {"file": "sub/a.log"}}, "program.file"),
    ({"engine": {"console": "yes"}}, "engine.console"),
    ({"engine": {"propagate": "yes"}}, "engine.propagate"),
    ({"program": {"propagate": True}}, "未知のキー"),                              # 親には流す先が無い
    ({"rotation": {"max_bytes": 0}}, "rotation.max_bytes"),
    ({"rotation": {"backup_count": -1}}, "rotation.backup_count"),
    ({"rotation": {"max_bytes": True}}, "rotation.max_bytes"),
    ({"rotation": {"unit": 1}}, "未知のキー"),
    ({"format": "%(levelname)s %(funcName)s %(message)s"}, "asctime"),            # タイムスタンプが無い
    ({"format": "%(asctime)s %(levelname)s %(message)s"}, "funcName"),            # メソッドが無い
    ({"format": "%(asctime)s %(funcName)s"}, "message"),                          # メッセージが無い
    ({"log_dir": ""}, "log_dir"),
    (["x"], "トップレベル"),
])
def test_invalid_configs_are_input_errors(data: object, needle: str) -> None:
    with pytest.raises(m.InputError, match=needle):
        lg.parse_logging_config(data, "t.yaml")


def test_config_file_errors_are_input_errors(tmp_path: pathlib.Path) -> None:
    with pytest.raises(m.InputError, match="ログ設定ファイルが無い"):
        lg.read_logging_config(tmp_path / "none.yaml")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(m.InputError, match="読めない"):
        lg.read_logging_config(bad)
    txt = tmp_path / "c.txt"
    txt.write_text("{}", encoding="utf-8")
    with pytest.raises(m.InputError, match="yaml"):
        lg.read_logging_config(txt)
    unreadable = tmp_path / "bad.yaml"
    unreadable.write_text("a: [1, 2\n", encoding="utf-8")
    with pytest.raises(m.InputError, match="読めない"):
        lg.read_logging_config(unreadable)


def test_config_resolution_order(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit, env = tmp_path / "a.yaml", tmp_path / "b.yaml"
    monkeypatch.setenv(lg.ENV_VAR, str(env))
    assert lg.find_logging_config(explicit) == explicit                    # 引数が最優先
    assert lg.find_logging_config() == env                                 # 次に環境変数
    monkeypatch.delenv(lg.ENV_VAR)
    assert lg.find_logging_config() == ROOT / "nurse.logging.yaml"         # 最後にリポジトリ直下
    assert lg.read_logging_config(ROOT / "nurse.logging.yaml") == lg.LoggingConfig()   # 同梱の設定は既定値と同じ


def test_library_is_silent_until_setup(capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path) -> None:
    """setup_logging を呼ばないライブラリ利用では、標準出力にもファイルにも何も出さず、求解も変わらない。"""
    lg.reset_logging()
    res = m.solve(m.synthetic(), m.Eps(), time_limit=60)
    assert res.ok
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
    assert not (tmp_path / "logs").exists()
    assert any(isinstance(h, logging.NullHandler) for h in logging.getLogger("nurse").handlers)


def test_setup_creates_parent_and_child_with_stdout_and_rotating_file(tmp_path: pathlib.Path) -> None:
    lg.setup_logging(_config(tmp_path, engine="level: INFO, propagate: false", extra="rotation: {max_bytes: 4096, backup_count: 3}"))
    parent, child = logging.getLogger("nurse"), logging.getLogger("nurse.engine")
    assert child.parent is parent                                                  # 親子関係
    for logger, name in ((parent, "nurse.log"), (child, "nurse_engine.log")):
        kinds = sorted(type(h).__name__ for h in logger.handlers if getattr(h, "_nurse_handler", False))
        assert kinds == ["RotatingFileHandler", "StreamHandler"]                  # 標準出力とファイルの両方
        rot = next(h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler))
        assert (rot.maxBytes, rot.backupCount, pathlib.Path(rot.baseFilename).name) == (4096, 3, name)
    assert parent.propagate is False and child.propagate is False
    lg.setup_logging(_config(tmp_path))                                            # 呼び直しても、ハンドラは増えない。既定は子も親へ流す
    assert child.propagate is True
    assert sorted(type(h).__name__ for h in parent.handlers if getattr(h, "_nurse_handler", False)) == ["RotatingFileHandler", "StreamHandler"]
    assert sorted(type(h).__name__ for h in child.handlers if getattr(h, "_nurse_handler", False)) == ["RotatingFileHandler"]   # 標準出力は親が出す
    default = lg.LoggingConfig()
    assert (default.max_bytes, default.backup_count) == (10 * 1024 * 1024, 5)     # ローテーションの既定


def test_file_rotation_keeps_the_configured_generations(tmp_path: pathlib.Path) -> None:
    lg.setup_logging(_config(tmp_path, program="level: INFO, console: false", extra="rotation: {max_bytes: 300, backup_count: 2}"))
    log = logging.getLogger("nurse.test")
    for i in range(60):
        log.info("行 %d %s", i, "x" * 30)
    names = sorted(p.name for p in (tmp_path / "logs").glob("nurse.log*"))
    assert names == ["nurse.log", "nurse.log.1", "nurse.log.2"]                    # 2世代まで残る


def test_every_line_has_timestamp_method_and_message(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    lg.setup_logging(_config(tmp_path))
    res = m.solve(m.synthetic(), m.Eps(), time_limit=60)
    assert res.ok
    for text in (_read(tmp_path, "nurse.log"), _read(tmp_path, "nurse_engine.log"), capsys.readouterr().out):
        lines = text.splitlines()
        assert lines and all(LINE.match(x) for x in lines), [x for x in lines if not LINE.match(x)][:3]
    assert "nurse.solver.solve:" in _read(tmp_path, "nurse.log")                   # メソッド（モジュール.関数）
    assert "nurse.engine.CP-SAT:" in _read(tmp_path, "nurse_engine.log")          # エンジンの行は、ソルバー名がメソッド欄に入る


def test_program_log_records_variables_constraints_solving_and_status(tmp_path: pathlib.Path) -> None:
    lg.setup_logging(_config(tmp_path))
    I = m.synthetic()
    res = m.solve(I, m.Eps(), time_limit=60)
    log = _read(tmp_path, "nurse.log")
    n_vars = sum(len(res.model.xv[n].flat) for n in res.model.N)
    assert f"変数の総数 {n_vars}" in log                                           # 変数宣言の総数
    assert f"制約の総数: {sum(res.counts.values())} 本（{len(res.counts)} 群）" in log   # 制約定義の総数
    for group, count in res.counts.items():
        assert re.search(rf"制約を定義: {re.escape(group)} \[.+\] {count} 本", log), group      # 群ごとの個別内容
    assert re.search(r"補助変数の総数: \d+（種類別 .+）", log)
    for step in ("求解開始: solver=ortools", "モデル構築 完了", "ソルバーへの変換 完了", "求解プロセス開始", "求解 終了: ok=True ステータス=FEASIBLE"):
        assert step in log, step                                                   # 制御・求解プロセス・ステータス
    assert "Starting CP-SAT solver" in log and "nurse.engine.CP-SAT:" in log     # 既定では、ソルバーの詳細も①に時系列で入る
    assert log.index("求解プロセス開始") < log.index("Starting CP-SAT solver") < log.index("求解 終了")   # 求解の流れの中に並ぶ


def test_engine_log_goes_to_its_own_file_and_flows_into_the_program_log(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    lg.setup_logging(_config(tmp_path))                                            # 既定: ②は①にも流す
    m.solve(m.synthetic(), m.Eps(), time_limit=60)
    engine, program, out = _read(tmp_path, "nurse_engine.log"), _read(tmp_path, "nurse.log"), capsys.readouterr().out
    assert "Starting CP-SAT solver" in engine and "CpSolverResponse summary" in engine and "status: " in engine
    assert "求解開始" not in engine                                                # ②には処理の流れは入らない
    engine_lines = engine.splitlines()
    assert all(x in program for x in engine_lines[:5] + engine_lines[-5:])        # ①にも同じ行が入る
    assert program.count("Starting CP-SAT solver") == 1                            # ①のファイルで二重にならない
    assert "求解開始" in out and out.count("Starting CP-SAT solver") == 1          # 標準出力にも出る。二重にならない
    assert out.count("CpSolverResponse summary") == 1


def test_engine_propagate_false_keeps_the_program_log_short(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    lg.setup_logging(_config(tmp_path, engine="level: INFO, propagate: false"))
    m.solve(m.synthetic(), m.Eps(), time_limit=60)
    program, out = _read(tmp_path, "nurse.log"), capsys.readouterr().out
    assert "Starting CP-SAT solver" not in program and "nurse.engine.CP-SAT:" not in program      # ①は処理の流れだけ
    assert "Starting CP-SAT solver" in _read(tmp_path, "nurse_engine.log")                  # ②は専用のファイル
    assert out.count("Starting CP-SAT solver") == 1 and "求解開始" in out                    # 標準出力には、①と②の両方（子が自分で出す）


def test_engine_console_off(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    lg.setup_logging(_config(tmp_path, engine="level: INFO, console: false, propagate: false"))
    m.solve(m.synthetic(), m.Eps(), time_limit=60)
    assert "Starting CP-SAT solver" not in capsys.readouterr().out and "Starting CP-SAT solver" in _read(tmp_path, "nurse_engine.log")


def test_engine_console_is_used_when_the_program_console_is_off(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """親が標準出力に出さないときは、②が流す設定でも、子が自分の標準出力に出す（一度だけ）。"""
    lg.setup_logging(_config(tmp_path, program="level: INFO, console: false"))
    m.solve(m.synthetic(), m.Eps(), time_limit=60)
    out = capsys.readouterr().out
    assert out.count("Starting CP-SAT solver") == 1 and "求解開始" not in out


def test_program_level_filters_the_engine_lines_that_flow_into_it(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    lg.setup_logging(_config(tmp_path, program="level: WARNING"))                  # ①は WARNING 以上だけ。②は INFO
    m.solve(m.synthetic(), m.Eps(), time_limit=60)
    assert _read(tmp_path, "nurse.log") == "" and capsys.readouterr().out == ""   # 親へ流れた②の INFO 行も、①のレベルで絞られる
    assert "Starting CP-SAT solver" in _read(tmp_path, "nurse_engine.log")         # ②の専用ファイルには残る


def test_engine_log_disabled_by_level_does_not_change_the_solve(tmp_path: pathlib.Path) -> None:
    lg.setup_logging(_config(tmp_path, engine="level: WARNING"))
    res = m.solve(m.synthetic(), m.Eps(), time_limit=60)
    assert res.ok and _read(tmp_path, "nurse_engine.log") == ""
    assert "求解 終了" in _read(tmp_path, "nurse.log")


def test_debug_level_lists_individual_constraints_and_variables(tmp_path: pathlib.Path) -> None:
    lg.setup_logging(_config(tmp_path, program="level: DEBUG, console: false"))
    res = m.solve(m.synthetic(start_date=datetime.date(2026, 11, 2)), m.Eps(), time_limit=60)
    log = _read(tmp_path, "nurse.log")
    assert len(re.findall(r"nurse\.kernel\.build_named: 制約: ", log)) == sum(res.counts.values())     # 個別の制約が全件
    assert "制約: cover-0001  [人数制約下限] 11/02 00:00 - 11/02 00:30 勤務者数=3" in log
    assert re.search(r"決定変数 x0: 形 \(35, \d+\)（取れる値 \d+ 個）", log)                                  # 個別の変数
    found = re.search(r"補助変数の総数: (\d+)", log)
    assert found is not None
    n_aux = int(found.group(1))
    assert len(re.findall(r"補助変数: ", log)) == n_aux > 0
    info = lg.setup_logging(_config(tmp_path, program="level: INFO, console: false"))                       # INFO では個別は出ない
    assert info.program.level == "INFO"


def test_config_file_input_is_logged(tmp_path: pathlib.Path) -> None:
    lg.setup_logging(_config(tmp_path, program="level: INFO, console: false"))
    props = tmp_path / "p.properties"
    props.write_text("solve.time_limit = 12\n", encoding="utf-8")
    m.load_settings(props)
    log = _read(tmp_path, "nurse.log")
    assert f"設定ファイルを読み込み: {props}（引数）" in log and "solve_time_limit=12.0" in log
    assert "ログ設定:" in log                                                      # ログ設定の読み込み自体も記録する


def test_diagnosis_logs_the_process_and_the_mus(tmp_path: pathlib.Path) -> None:
    lg.setup_logging(_config(tmp_path, program="level: DEBUG, console: false"))
    I = m.synthetic()
    d = m.diagnose(dc.replace(I, off_days=[I.n_days + 1] + I.off_days[1:]), m.Eps(), time_limit=60)     # 公休が日数を超える矛盾
    assert d.status == m.CONFLICT
    log = _read(tmp_path, "nurse.log")
    for step in ("診断開始", "通常の求解: ok=False ステータス=UNSATISFIABLE", "制約を分類", "MUS の事前候補（コア）", "MUS（子プロセス）を開始",
                 "MUS（子プロセス）が時間内に終了", "MUS 終了: 最小", "診断結果: ステータス=conflict コード=CONFLICT(20)"):
        assert step in log, step
    assert re.search(r"MUS: 1 件（前提 0・soft 1）、最小=True、前提だけの矛盾=False、群=\['offcount'\]", log)    # 結果の MUS
    assert "MUS の要素: offcount-0001" in log                                      # 個別の要素（DEBUG）
    assert "Starting CP-SAT" in _read(tmp_path, "nurse_engine.log")               # 診断の通常求解も、エンジンのログに残る


def test_cli_logs_the_control_flow_and_option(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _config(tmp_path, engine="level: INFO, console: false")
    assert m.main(["--log-config", str(cfg), "--time-limit", "60"]) == ReturnCode.OK
    log = _read(tmp_path, "nurse.log")
    for step in ("ログ設定:", "起動: lin=1", "合成インスタンスを作成", "事前検査: OK", "求解開始", "終了: 解が見つかった（リターンコード 0）",
                 "終了コード: 0 OK"):
        assert step in log, step
    assert "起動: lin=1" in capsys.readouterr().out                                # 標準出力にも出る


def test_cli_with_an_invalid_log_config_is_an_input_error(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("format: '%(message)s'\n", encoding="utf-8")
    assert m.main(["--log-config", str(bad)]) == ReturnCode.INPUT_ERROR
    assert "asctime" in capsys.readouterr().err
    assert m.main(["--log-config", str(tmp_path / "none.yaml")]) == ReturnCode.INPUT_ERROR


def test_method_column_is_the_public_function_not_a_helper(tmp_path: pathlib.Path) -> None:
    """ヘルパー関数（`_` で始まる）のログは、呼び出し元の公開の関数のメソッド名で出す（`stacklevel`）。
    求解・診断（MUS）・設定ファイルの読み込みのログで、メソッド欄に内部の関数名が出ない。"""
    lg.setup_logging(_config(tmp_path, program="level: DEBUG, console: false", engine="level: INFO, console: false"))
    I = m.synthetic()
    m.load_settings(ROOT / "nurse.properties")
    m.diagnose(dc.replace(I, off_days=[I.n_days + 1] + I.off_days[1:]), m.Eps(), time_limit=60)         # 求解・診断・MUS
    log = _read(tmp_path, "nurse.log")
    methods = {x.split(" ")[3].rstrip(":") for x in log.splitlines()}
    assert {"nurse.kernel.build_named", "nurse.diagnosis.diagnose", "nurse.solver.solve",
            "nurse.config.load_settings", "nurse.logger.setup_logging"} <= methods
    private = {x for x in methods if re.search(r"\._[a-z]", x)}                                       # `__init__` などの特殊メソッドは除く
    assert not private, private


def test_diagnosis_does_not_rebuild_the_constraints(tmp_path: pathlib.Path) -> None:
    """診断は、求解で作った制約（Named）を再利用する。制約の定義・総数・個別内容のログが、求解と診断で重複しない。"""
    lg.setup_logging(_config(tmp_path, program="level: DEBUG, console: false", engine="level: INFO, console: false, propagate: false"))
    I = m.synthetic(start_date=datetime.date(2026, 11, 2))
    d = m.diagnose(dc.replace(I, off_days=[I.n_days + 1] + I.off_days[1:]), m.Eps(), time_limit=60)
    assert d.status == m.CONFLICT
    log = _read(tmp_path, "nurse.log")
    assert log.count("制約の総数:") == 1 and log.count("補助変数の総数:") == 1             # 作り直さないので、1回だけ
    assert log.count("制約: cover-0001 ") == 1 and log.count("補助変数: wk_0_12") == 1
    assert "定義済みの制約を再利用（作り直さない）" in log                                # 診断が再利用した記録（DEBUG）
    assert log.index("通常の求解: ok=False") < log.index("制約を分類")
