"""MUS プロセス（`nurse.mus_worker`）とやりとりするメッセージの定義と入出力。**`exact` を import しない**。

診断（`nurse.diagnosis`）は、MUS（矛盾の原因の最小集合）の計算を別プロセスに任せる。やりとりは、作業用の一時ディレクトリの
ファイル 4 つだけで行う（標準入出力は使わない。ソルバーが何を書いても、メッセージが壊れないため）。

| ファイル | 書く側 | 内容 |
|---|---|---|
| `request.pkl` | 診断 | 問い合わせ（hard・soft の式、ソルバー名、時間制限） |
| `ready` | MUS プロセス | 起動と式の復元が終わった印（中身なし）。ここから時間制限を数える |
| `core.pkl` | MUS プロセス | 事前候補（仮定つきの求解で得た、最小とは限らない矛盾の集合） |
| `response.pkl` | MUS プロセス | 最終結果（MUS・充足可能・事前候補なし・ソルバーが使えない・失敗） |

request は、辞書を **1 回の `pickle.dump`** で書く。hard と soft を別々に pickle すると、同じ変数が別オブジェクトに複製されて
意味が壊れる。1 つのオブジェクトにまとめると、共有された変数の同一性が保たれる。response と core は、制約そのものを返さず、
soft の添字だけを返す（診断が、自分の持つ式・名前つき制約と添字で対応づける）。

注意: pickle は任意のコードを実行しうる。このモジュールは、**自分が作った一時ディレクトリ（作成者だけが読み書きできる）の
ファイルだけ**を読む前提で、信頼できない入力を読み込まない。ネットワークや、他の利用者が書けるディレクトリ経由では使わない。
"""
from __future__ import annotations
import os
import pickle
from pathlib import Path
from typing import Any
import cpmpy

PROTOCOL_VERSION = 1
"""メッセージの版。request と response で一致しなければ、MUS プロセスは ERROR を返す。"""

MUS, SAT, UNKNOWN, UNAVAILABLE, ERROR = "MUS", "SAT", "UNKNOWN", "UNAVAILABLE", "ERROR"
STATUSES = (MUS, SAT, UNKNOWN, UNAVAILABLE, ERROR)
"""response の状態。MUS=最小の矛盾が求まった、SAT=モデルが充足可能（MUS はない）、UNKNOWN=事前候補が時間内に取れなかった、
UNAVAILABLE=ソルバーが使えない、ERROR=それ以外の失敗。"""

REQUEST_FILE, READY_FILE, CORE_FILE, RESPONSE_FILE = "request.pkl", "ready", "core.pkl", "response.pkl"

REQUEST_KEYS = ("protocol_version", "cpmpy_version", "hard", "soft", "solver", "time_limit")
CORE_KEYS = ("protocol_version", "core_indices", "elapsed_sec")
RESPONSE_KEYS = ("protocol_version", "status", "mus_indices", "timings", "message")


class ProtocolError(RuntimeError):
    """メッセージの形が仕様と違う（読めない・キーがない）。"""


def make_request(hard: list[Any], soft: list[Any], solver: str, time_limit: float) -> dict[str, Any]:
    """request を作る

    Args:
        hard: 常に成り立つ制約（CPMpy の式）。
        soft: MUS の候補。各要素は 1 つの式（リストや and で束ねない）。
        solver: MUS を求めるソルバー名（`exact` など）。
        time_limit: 事前候補の求解の時間制限（秒）。MUS 本体の打ち切りは、呼び出し側がプロセスを止めて行う。

    Returns:
        request の辞書。"""
    return {"protocol_version": PROTOCOL_VERSION, "cpmpy_version": cpmpy.__version__, "hard": list(hard), "soft": list(soft),
            "solver": solver, "time_limit": time_limit}


def make_core(core_indices: list[int], elapsed_sec: float) -> dict[str, Any]:
    """事前候補（コア）のメッセージを作る

    Args:
        core_indices: 事前候補に含まれる soft の添字。
        elapsed_sec: 事前候補の求解にかかった秒数。

    Returns:
        core の辞書。"""
    return {"protocol_version": PROTOCOL_VERSION, "core_indices": sorted(core_indices), "elapsed_sec": float(elapsed_sec)}


def make_response(status: str, mus_indices: list[int], timings: dict[str, float], message: str = "") -> dict[str, Any]:
    """response を作る。message には、変数名や制約の中身を書かない

    Args:
        status: `STATUSES` のどれか。
        mus_indices: MUS に含まれる soft の添字（制約そのものは返さない）。
        timings: 秒数の内訳（`core`: 事前候補の求解、`mus`: MUS の計算）。
        message: 失敗の理由など。

    Returns:
        response の辞書。"""
    if status not in STATUSES:
        raise ValueError(f"未知の status: {status!r}")
    return {"protocol_version": PROTOCOL_VERSION, "status": status, "mus_indices": sorted(mus_indices),
            "timings": {k: float(v) for k, v in timings.items()}, "message": message}


def write_message(path: str | Path, message: dict[str, Any]) -> None:
    """メッセージを書く。一時ファイルに書いてから置き換えるので、読む側が書きかけを読むことはない

    Args:
        path: 書き込み先。
        message: `make_request`・`make_core`・`make_response` の戻り値。"""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(message, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)


def read_message(path: str | Path, keys: tuple[str, ...], what: str) -> dict[str, Any]:
    """メッセージを読み、版と必要なキーを確かめる。形が違えば `ProtocolError`

    Args:
        path: 読み込むファイル（自分が作った一時ディレクトリのもの）。
        keys: 必要なキー（`REQUEST_KEYS`・`CORE_KEYS`・`RESPONSE_KEYS`）。
        what: エラーメッセージに使う名前（`request` など）。

    Returns:
        メッセージの辞書。"""
    try:
        with open(path, "rb") as f:
            obj = pickle.load(f)
    except Exception as e:                                                # 壊れている・読めない（内容は書かない）
        raise ProtocolError(f"{what} を読めない: {type(e).__name__}") from None
    if not isinstance(obj, dict):
        raise ProtocolError(f"{what} が辞書ではない: {type(obj).__name__}")
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ProtocolError(f"{what} にキーがない: {missing}")
    if obj["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolError(f"{what} の protocol_version が不一致: {obj['protocol_version']}（期待 {PROTOCOL_VERSION}）")
    return obj
