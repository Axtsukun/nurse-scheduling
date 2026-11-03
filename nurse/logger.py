"""ログの設定（標準出力とファイルの両方へ。親子の2系統）。

- **親 `nurse`（①プログラム全体のログ）**: ファイル入出力、制御プロセス、変数宣言（個別内容と総数）、制約定義（個別内容と総数）、
  求解プロセス、ステータス、結果（ステータス・MUS）。モジュールごとの `logging.getLogger(__name__)` はすべてこの子孫で、
  親のハンドラに流れる。
- **子 `nurse.engine`（②エンジン求解詳細ログ）**: ソルバー（CP-SAT）の標準ログ。専用のファイルと標準出力に出す。
  既定では親へ伝えない（`propagate: false`。親の出力に CP-SAT の数百行が混ざらないようにする）。

設定は YAML か JSON（拡張子で判別）。メッセージの書式には、**タイムスタンプ（`asctime`）・メソッド（`funcName`）・メッセージ
（`message`）を必ず含める**（含まない設定は `InputError`）。ファイルは `RotatingFileHandler`（既定は 10MB × 5世代）。

ライブラリとして使うときは、`setup_logging` を呼ばない限り何も出力しない（`nurse` に `NullHandler` を付けてある）。
CLI（`nurse_csp.py`）は起動時に `setup_logging` を呼ぶ。

```yaml
log_dir: logs                    # ログの出力先ディレクトリ
format: "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s.%(funcName)s: %(message)s"
datefmt: "%Y-%m-%d %H:%M:%S"
rotation: {max_bytes: 10485760, backup_count: 5}
program: {level: INFO, file: nurse.log, console: true}
engine: {level: INFO, file: nurse_engine.log, console: true, propagate: false}
```
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .codes import InputError

PROGRAM_LOGGER = "nurse"
"""①プログラム全体のログの名前（親）。"""
ENGINE_LOGGER = "nurse.engine"
"""②エンジン求解詳細ログの名前（子）。"""

DEFAULT_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s.%(funcName)s: %(message)s"
"""既定の書式。タイムスタンプ・メソッド（`funcName`）・メッセージを含む。"""
DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"
REQUIRED_FIELDS = ("asctime", "funcName", "message")
"""書式に必ず含める項目（タイムスタンプ・メソッド・メッセージ）。"""
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
ENV_VAR = "NURSE_LOGGING"
"""設定ファイルのパスを指定する環境変数。"""
DEFAULT_FILES = ("nurse.logging.yaml", "nurse.logging.yml", "nurse.logging.json")
"""リポジトリ直下で探す設定ファイル名（先に見つかったもの）。"""
_ROOT = Path(__file__).resolve().parent.parent
_MARK = "_nurse_handler"                                   # このモジュールが付けたハンドラの目印


@dataclass(frozen=True)
class SinkConfig:
    """1系統（親または子）のログの出力設定。"""
    level: str = "INFO"             # 出力するレベル（DEBUG / INFO / WARNING / ERROR / CRITICAL）
    file: str = "nurse.log"         # ログファイル名（`log_dir` の下）
    console: bool = True            # 標準出力にも出すか
    propagate: bool = False         # 親のハンドラへも流すか（子 `engine` のみ意味がある）


@dataclass(frozen=True)
class LoggingConfig:
    """ログ設定の全体。YAML / JSON から作る（`parse_logging_config`）。"""
    log_dir: str = "logs"                                   # ログの出力先ディレクトリ
    format: str = DEFAULT_FORMAT                            # メッセージの書式（`REQUIRED_FIELDS` を含むこと）
    datefmt: str = DEFAULT_DATEFMT                          # `asctime` の日時の書式
    max_bytes: int = 10 * 1024 * 1024                       # ファイルローテーション: 1ファイルの最大バイト数
    backup_count: int = 5                                   # ファイルローテーション: 残す世代数
    program: SinkConfig = field(default_factory=SinkConfig)                                         # ①親
    engine: SinkConfig = field(default_factory=lambda: SinkConfig(file="nurse_engine.log"))         # ②子


def _check_keys(data: dict[str, Any], allowed: tuple[str, ...], where: str) -> None:
    """未知のキーがあれば `InputError`。

    Args:
        data: 調べる辞書。
        allowed: 使えるキー。
        where: エラー文に出す場所（入力元とキーの位置）。"""
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise InputError(f"{where}: 未知のキー {unknown}（使えるキー: {', '.join(allowed)}）")


def _section(data: dict[str, Any], key: str, source: str) -> dict[str, Any]:
    """辞書の節を取り出す（無ければ空）。辞書でなければ `InputError`。

    Args:
        data: 親の辞書。
        key: 取り出す節の名前。
        source: エラー文に出す入力元。

    Returns:
        節の辞書。"""
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise InputError(f"{source}: {key} は辞書でなければならない: {value!r}")
    return value


def _sink(data: dict[str, Any], key: str, default: SinkConfig, source: str) -> SinkConfig:
    """`program` / `engine` の節を `SinkConfig` にする。

    Args:
        data: 設定全体の辞書。
        key: 節の名前（`program` または `engine`）。
        default: 省略されたキーの既定値。
        source: エラー文に出す入力元。

    Returns:
        検証済みの出力設定。"""
    sec = _section(data, key, source)
    where = f"{source}:{key}"
    _check_keys(sec, ("level", "file", "console", "propagate"), where)
    level = str(sec.get("level", default.level)).upper()
    if level not in LEVELS:
        raise InputError(f"{where}.level は {', '.join(LEVELS)} のいずれか: {sec['level']!r}")
    file = sec.get("file", default.file)
    if not isinstance(file, str) or not file or Path(file).name != file:
        raise InputError(f"{where}.file はディレクトリを含まないファイル名でなければならない: {file!r}")
    flags: dict[str, bool] = {}
    for name, fallback in (("console", default.console), ("propagate", default.propagate)):
        flag = sec.get(name, fallback)
        if not isinstance(flag, bool):
            raise InputError(f"{where}.{name} は true / false でなければならない: {flag!r}")
        flags[name] = flag
    return SinkConfig(level, file, flags["console"], flags["propagate"])


def parse_logging_config(data: object, source: str = "<dict>") -> LoggingConfig:
    """YAML / JSON を読んだ辞書を、検証して `LoggingConfig` にする。不正は `InputError`。

    Args:
        data: 設定の辞書（トップレベルは辞書。空なら全て既定値）。
        source: エラー文に出す入力元の名前（ファイル名など）。

    Returns:
        検証済みのログ設定。省略したキーは既定値。"""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise InputError(f"{source}: 設定のトップレベルは辞書でなければならない: {type(data).__name__}")
    _check_keys(data, ("log_dir", "format", "datefmt", "rotation", "program", "engine"), source)
    default = LoggingConfig()
    log_dir = data.get("log_dir", default.log_dir)
    if not isinstance(log_dir, str) or not log_dir:
        raise InputError(f"{source}: log_dir は空でない文字列でなければならない: {log_dir!r}")
    fmt = data.get("format", default.format)
    if not isinstance(fmt, str):
        raise InputError(f"{source}: format は文字列でなければならない: {fmt!r}")
    missing = [f for f in REQUIRED_FIELDS if not re.search(rf"%\({f}\)", fmt)]
    if missing:
        raise InputError(f"{source}: format に必須の項目が無い: {['%(' + f + ')s' for f in missing]}"
                         "（タイムスタンプ・メソッド・メッセージは必ず含める）")
    datefmt = data.get("datefmt", default.datefmt)
    if not isinstance(datefmt, str):
        raise InputError(f"{source}: datefmt は文字列でなければならない: {datefmt!r}")
    rot = _section(data, "rotation", source)
    _check_keys(rot, ("max_bytes", "backup_count"), f"{source}:rotation")
    numbers: dict[str, int] = {}
    for name, fallback in (("max_bytes", default.max_bytes), ("backup_count", default.backup_count)):
        value = rot.get(name, fallback)
        if isinstance(value, bool) or not isinstance(value, int) or value < (1 if name == "max_bytes" else 0):
            raise InputError(f"{source}:rotation.{name} は{'正' if name == 'max_bytes' else '0 以上'}の整数でなければならない: {value!r}")
        numbers[name] = value
    return LoggingConfig(log_dir, fmt, datefmt, numbers["max_bytes"], numbers["backup_count"],
                         _sink(data, "program", default.program, source), _sink(data, "engine", default.engine, source))


def read_logging_config(path: str | os.PathLike[str]) -> LoggingConfig:
    """設定ファイル（`.yaml` / `.yml` / `.json`）を読んで検証する。

    Args:
        path: 設定ファイルのパス。

    Returns:
        検証済みのログ設定。ファイルが無い・読めない・形式が不正なら `InputError`。"""
    p = Path(path)
    if not p.is_file():
        raise InputError(f"ログ設定ファイルが無い: {p}")
    if p.suffix.lower() not in (".yaml", ".yml", ".json"):
        raise InputError(f"{p}: ログ設定は .yaml / .yml / .json のいずれか")
    try:
        text = p.read_text(encoding="utf-8")
        data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
    except (OSError, ValueError, yaml.YAMLError) as e:
        raise InputError(f"{p}: ログ設定を読めない: {e}") from None
    return parse_logging_config(data, str(p))


def find_logging_config(path: str | os.PathLike[str] | None = None) -> Path | None:
    """使うログ設定ファイルを探す。優先順は、引数のパス、環境変数 `NURSE_LOGGING`、リポジトリ直下の `nurse.logging.yaml`（等）。

    Args:
        path: 設定ファイルのパス。省略すると環境変数、リポジトリ直下の順に探す。

    Returns:
        見つかったパス。無ければ `None`（既定値を使う）。引数・環境変数で指定したのに無い場合は `InputError`。"""
    if path is not None:
        return Path(path)
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env)
    return next((p for p in (_ROOT / n for n in DEFAULT_FILES) if p.is_file()), None)


def reset_logging() -> None:
    """`setup_logging` が付けたハンドラをすべて外して閉じる（再設定の前、テストの後始末に使う）。"""
    for name in (PROGRAM_LOGGER, ENGINE_LOGGER):
        logger = logging.getLogger(name)
        for h in [h for h in logger.handlers if getattr(h, _MARK, False)]:
            logger.removeHandler(h)
            h.close()
        logger.propagate = True
        logger.setLevel(logging.NOTSET)


def _handlers(cfg: LoggingConfig, sink: SinkConfig) -> list[logging.Handler]:
    """1系統分のハンドラ（標準出力・ローテーションするファイル）を作る。

    Args:
        cfg: ログ設定の全体。
        sink: この系統の出力設定。

    Returns:
        標準出力（`console` が true のとき）と、ローテーションするファイルのハンドラ。"""
    formatter = logging.Formatter(cfg.format, cfg.datefmt)
    out: list[logging.Handler] = []
    if sink.console:
        out.append(logging.StreamHandler(sys.stdout))
    directory = Path(cfg.log_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        out.append(logging.handlers.RotatingFileHandler(directory / sink.file, maxBytes=cfg.max_bytes,
                                                        backupCount=cfg.backup_count, encoding="utf-8"))
    except OSError as e:
        raise InputError(f"ログファイルを作れない: {directory / sink.file}: {e}") from None
    for h in out:
        h.setFormatter(formatter)
        h.setLevel(logging.NOTSET)
        setattr(h, _MARK, True)
    return out


def setup_logging(path: str | os.PathLike[str] | None = None, config: LoggingConfig | None = None) -> LoggingConfig:
    """ログを設定する（呼ぶたびに設定し直す）。親 `nurse`（①）と子 `nurse.engine`（②）に、標準出力とファイルのハンドラを付ける。

    Args:
        path: 設定ファイルのパス。省略すると環境変数 `NURSE_LOGGING`、リポジトリ直下の `nurse.logging.yaml` の順に探し、
            どれも無ければ既定値。
        config: 設定オブジェクト（指定するとファイルは読まない。主にテスト用）。

    Returns:
        使った設定。"""
    source: Path | None = None
    if config is None:
        source = find_logging_config(path)
        config = read_logging_config(source) if source is not None else LoggingConfig()
    reset_logging()
    for name, sink in ((PROGRAM_LOGGER, config.program), (ENGINE_LOGGER, config.engine)):
        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, sink.level))
        logger.propagate = sink.propagate if name == ENGINE_LOGGER else False
        for h in _handlers(config, sink):
            logger.addHandler(h)
    logging.getLogger(__name__).info("ログ設定: %s / 出力先 %s（プログラム %s・エンジン %s、ローテーション %d バイト × %d 世代）",
                                     source if source is not None else "既定値", Path(config.log_dir).resolve(),
                                     config.program.file, config.engine.file, config.max_bytes, config.backup_count)
    return config


class _EngineSink:
    """ソルバーの標準ログを、行ごとに `nurse.engine` のログへ流す。"""

    def __init__(self, label: str) -> None:
        """
        Args:
            label: ログの「メソッド」欄に出すソルバーの名前。"""
        self.label = label                                  # ログの「メソッド」欄に出す名前（例: CP-SAT）
        self.logger = logging.getLogger(ENGINE_LOGGER)      # 出力先

    def __call__(self, text: str) -> None:
        """ソルバーが出した1回分のテキスト（複数行のことがある）を、空でない行ごとに記録する。

        Args:
            text: ソルバーのログのテキスト。"""
        for line in text.splitlines():
            if line.strip():
                self.logger.handle(self.logger.makeRecord(ENGINE_LOGGER, logging.INFO, __file__, 0, line.rstrip(), (), None,
                                                          func=self.label))


def attach_engine_log(solver_name: str, solver: Any) -> None:
    """求解ソルバーの標準ログを `nurse.engine` に流す設定をする（`solve()` の前に呼ぶ）。

    CP-SAT（`ortools`）のときだけ有効。検索ログを有効にし、ソルバーが標準出力へ直接出さない（`log_to_stdout=False`）ように
    して、ログは `nurse.engine` のハンドラ（標準出力・ファイル）だけから出す。エンジンのログが無効（レベルが INFO より上、
    またはログ未設定）なら、何もしない（求解は変わらない）。

    CPMpy の `solve(log_search_progress=True)` は使わない。標準出力が差し替えられているとき（IPython・pytest・リダイレクト）に、
    CPMpy がログのコールバックを `print` に上書きしてしまうため。CP-SAT のパラメータを直接設定する。

    Args:
        solver_name: ソルバー名（`ortools` など）。
        solver: CPMpy のソルバーオブジェクト（`cp.SolverLookup.get` の戻り値）。"""
    if solver_name != "ortools" or not logging.getLogger(ENGINE_LOGGER).isEnabledFor(logging.INFO):
        return
    solver.ort_solver.log_callback = _EngineSink("CP-SAT")
    solver.ort_solver.parameters.log_search_progress = True
    solver.ort_solver.parameters.log_to_stdout = False
