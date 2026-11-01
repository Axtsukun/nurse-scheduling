"""設定ファイル（Java 形式の properties）。時間制限を、コードを変えずに変更できるようにする。

```properties
# nurse.properties
solve.time_limit = 30     # 求解の時間制限（秒）
mus.time_limit = 30       # MUS（矛盾の原因の絞り込み）1回あたりの時間制限（秒）
```

読み込む場所（先に見つかったもの）:

1. 関数に渡したパス（`load_settings(path)`。CLI の `--properties`）。ファイルが無ければ InputError
2. 環境変数 `NURSE_PROPERTIES` のパス。ファイルが無ければ InputError
3. リポジトリ直下の `nurse.properties`。無ければ既定値（下の Settings）

書式: `キー=値`（空白は無視）。`#` か `!` で始まる行と空行はコメント。未知のキー・重複・正でない数・数でない値は InputError。

`solve()` と `diagnose()` は、time_limit / mus_time_limit を省略したときにこの設定を使う（引数で渡せば引数が優先）。"""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from .codes import InputError

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "nurse.properties"
ENV_VAR = "NURSE_PROPERTIES"
KEYS = {"solve.time_limit": "solve_time_limit", "mus.time_limit": "mus_time_limit"}     # properties のキー → Settings の属性


@dataclass(frozen=True)
class Settings:
    """設定値。ファイルに無いキーは既定値"""
    solve_time_limit: float = 30.0          # 求解の時間制限（秒）
    mus_time_limit: float = 30.0            # MUS 1回あたりの時間制限（秒。soft の MUS と前提の MUS にそれぞれ適用）


def parse_properties(text: str, source: str = "<text>") -> Settings:
    """properties の文字列 → Settings。不正は InputError（どのファイルの何行目かを示す）"""
    found: dict[str, float] = {}
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line[0] in "#!":
            continue
        key, sep, value = (p.strip() for p in line.partition("="))
        where = f"{source}:{no}"
        if not sep:
            raise InputError(f"{where}: `キー=値` の形式ではない: {raw!r}")
        if key not in KEYS:
            raise InputError(f"{where}: 未知のキー {key!r}（使えるキー: {', '.join(KEYS)}）")
        if key in found:
            raise InputError(f"{where}: キー {key!r} が重複している")
        try:
            number = float(value)
        except ValueError:
            raise InputError(f"{where}: {key} は数（秒）でなければならない: {value!r}") from None
        if not number > 0:                                              # NaN も弾く
            raise InputError(f"{where}: {key} は正の数（秒）でなければならない: {value!r}")
        found[key] = number
    return Settings(**{KEYS[k]: v for k, v in found.items()})


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    """設定を読む（呼ぶたびに読み直す。ファイルを書き換えると次の呼び出しから反映される）"""
    explicit = path if path is not None else os.environ.get(ENV_VAR)
    if explicit is not None:
        p = Path(explicit)
        if not p.is_file():
            raise InputError(f"設定ファイルが見つからない: {p}（{'引数' if path is not None else '環境変数 ' + ENV_VAR}）")
        return parse_properties(p.read_text(encoding="utf-8"), str(p))
    if DEFAULT_PATH.is_file():
        return parse_properties(DEFAULT_PATH.read_text(encoding="utf-8"), str(DEFAULT_PATH))
    return Settings()
