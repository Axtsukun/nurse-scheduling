"""リターンコード（終了コード）と例外。想定される状態（正常完了・時間打ち切り・矛盾）と失敗の種類を、番号と名前で区別する。

| 番号 | 名前 | 意味 |
|---|---|---|
| 0 | `OK` | 正常完了: 解が見つかった |
| 10 | `SOLVE_TIME_LIMIT` | 求解が時間切れ: 解も、解が無い証明も得られなかった（制約矛盾かどうかは不明） |
| 20 | `CONFLICT` | 制約矛盾: 解が無いと証明でき、矛盾の原因（MUS）を最小まで絞れた |
| 21 | `CONFLICT_MUS_TIME_LIMIT` | 制約矛盾: 原因の絞り込みが時間切れ。事前候補を返した（最小でない可能性） |
| 22 | `CONFLICT_UNIDENTIFIED` | 制約矛盾: 証明済みだが、原因の制約を時間内に特定できなかった |
| 30 | `INPUT_ERROR` | 入力・設定の誤り（Instance の不整合、nurse.properties の不正など）→ `InputError` |
| 40 | `SOLVER_UNAVAILABLE` | ソルバーや実行環境が使えない（exact 未導入、fork が使えないなど）→ `SolverUnavailableError` |
| 50 | `INTERNAL_ERROR` | 内部エラー（モデルの不具合、想定外の例外）→ `InternalError` |

（2 は argparse の引数エラー。Python の慣習に従う）

0 は正常、10 台は時間打ち切り、20 台は制約矛盾（解なし）、30 以上は異常。CLI の終了コードにも使う（nurse_csp.py）。"""
from __future__ import annotations
from enum import IntEnum


class ReturnCode(IntEnum):
    """リターンコード。値の意味はモジュールの説明の表を参照。`label` に日本語の説明がある。"""
    OK = 0  # 正常完了: 解が見つかった
    SOLVE_TIME_LIMIT = 10  # 求解が時間切れ（制約矛盾かどうかは不明）
    CONFLICT = 20  # 制約矛盾。原因（MUS）を最小まで特定
    CONFLICT_MUS_TIME_LIMIT = 21  # 制約矛盾。原因の絞り込みが時間切れ（最小でない可能性）
    CONFLICT_UNIDENTIFIED = 22  # 制約矛盾。原因を時間内に特定できなかった
    INPUT_ERROR = 30  # 入力・設定の誤り → InputError
    SOLVER_UNAVAILABLE = 40  # ソルバー・実行環境が使えない → SolverUnavailableError
    INTERNAL_ERROR = 50  # 内部エラー → InternalError

    @property
    def label(self) -> str:
        """日本語の説明（CLI の表示用）"""
        return _LABELS[self]

    @property
    def is_error(self) -> bool:
        """異常終了（30 以上）か。時間打ち切りと制約矛盾は、想定される結果なので異常ではない"""
        return self >= ReturnCode.INPUT_ERROR


_LABELS = {
    ReturnCode.OK: "正常完了（解が見つかった）",
    ReturnCode.SOLVE_TIME_LIMIT: "求解が時間切れ（解も矛盾の証明も得られなかった）",
    ReturnCode.CONFLICT: "制約矛盾（解なし。原因を最小まで特定）",
    ReturnCode.CONFLICT_MUS_TIME_LIMIT: "制約矛盾（解なし。原因の絞り込みが時間切れ。最小でない可能性）",
    ReturnCode.CONFLICT_UNIDENTIFIED: "制約矛盾（解なし。原因を時間内に特定できなかった）",
    ReturnCode.INPUT_ERROR: "入力・設定の誤り",
    ReturnCode.SOLVER_UNAVAILABLE: "ソルバー・実行環境が使えない",
    ReturnCode.INTERNAL_ERROR: "内部エラー",
}


class NurseError(Exception):
    """このエンジンが意図して投げる例外の基底。code が対応するリターンコード"""
    code = ReturnCode.INTERNAL_ERROR  # 対応するリターンコード


class InputError(NurseError, ValueError):
    """入力・設定の誤り（Instance の不整合、nurse.properties の不正、外せない制約を外そうとした、など）"""
    code = ReturnCode.INPUT_ERROR  # 対応するリターンコード


class SolverUnavailableError(NurseError, RuntimeError):
    """ソルバーや実行環境が使えない（exact 未導入、fork が使えない環境など）。黙って別の方法に替えない"""
    code = ReturnCode.SOLVER_UNAVAILABLE  # 対応するリターンコード


class InternalError(NurseError, RuntimeError):
    """内部エラー（モデルの不具合、MUS の計算の失敗など）"""
    code = ReturnCode.INTERNAL_ERROR  # 対応するリターンコード


def code_of(exc: BaseException) -> ReturnCode:
    """例外 → リターンコード。NurseError はその code、それ以外の想定外の例外は INTERNAL_ERROR"""
    return exc.code if isinstance(exc, NurseError) else ReturnCode.INTERNAL_ERROR
