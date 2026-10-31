"""
制約メッセージの構造化データ（対象・パラメータ）と、それを文字にする整形器。
仕様は docs/design/constraint-message-spec.md。

  メッセージ = `<対象> <名前><演算子><値> <名前><演算子><値> …`   例: 11/03 12:30 - 11/03 13:00 勤務者数=10

制約メソッドは、文字列を組み立てず、部品（Nurse・Day・Span など）と Param を返す。日付表記・看護師名・区分名の
方針（Instance の start_date・nurse_names・cat_names）は、整形器（Renderer）の1か所に集める。
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
from .instance import Instance, DAY

WEEKDAYS = "月火水木金土日"
CAT_NAMES = {"D": "日勤", "E": "準夜", "N": "深夜", "P": "パート", "OFF": "公休", "LV": "有休"}   # ShiftDef.cat の既定の表示名


# =============================================================================
# 構造化データ（target の部品と、パラメータ）
# =============================================================================
@dataclass(frozen=True)
class Nurse:
    n: int                       # 看護師の添字


@dataclass(frozen=True)
class Day:
    d: int                       # 計画日（0 始まり。負は履歴＝前月末）


@dataclass(frozen=True)
class DaySpan:
    d0: int                      # 日の範囲（両端を含む）
    d1: int


@dataclass(frozen=True)
class Span:
    a: int                       # 時間帯 [a, b)。計画日0の 0:00 を 0 とするスロット（30分単位。48 で翌日 0:00）
    b: int


@dataclass(frozen=True)
class Cat:
    k: str                       # 勤務区分（ShiftDef.cat）


@dataclass(frozen=True)
class Shift:
    s: int                       # 勤務の添字


@dataclass(frozen=True)
class Clock:
    slot: int                    # 時刻（その日の 0:00 からのスロット。日をまたぐと 48 以上）


@dataclass(frozen=True)
class Param:
    """パラメータ「名前<演算子>値」。値は数値・文字列・上の部品。単位は数値のあとに付ける"""
    name: str
    value: object
    unit: str = ""
    op: str = "="


# =============================================================================
# 整形器
# =============================================================================
class Renderer:
    """構造化データを文字にする。表示方針（開始日・看護師名・区分名）は Instance から取る"""

    def __init__(self, I: Instance):
        self.I = I

    # ---- 日付・時刻 ----
    def date(self, d: int) -> str:
        """計画日 d の表示。開始日あり: MM/DD。なし: 6日目(土)、履歴は 前月末2日前"""
        I = self.I
        if I.start_date is not None:
            return (I.start_date + timedelta(days=d)).strftime("%m/%d")
        if d < 0:
            return f"前月末{-d}日前"
        return f"{d + 1}日目({WEEKDAYS[(I.start_weekday + d) % 7]})"

    @staticmethod
    def hhmm(slot: int) -> str:
        """日の中の時刻（0〜47 スロット）→ HH:mm（ゼロ埋め）"""
        return f"{slot // 2:02d}:{30 * (slot % 2):02d}"

    def stamp(self, abs_slot: int) -> str:
        """絶対スロット（計画日0の 0:00 = 0）→ `MM/DD HH:mm`。48 の倍数は翌日の 00:00 になる（24:00 とは書かない）"""
        d, s = divmod(abs_slot, DAY)
        return f"{self.date(d)} {self.hhmm(s)}"

    def clock(self, slot: int) -> str:
        """時刻（日付なし）。翌日にかかる時刻（48 以上）は `翌HH:mm`"""
        return self.hhmm(slot) if slot < DAY else f"翌{self.hhmm(slot - DAY)}"

    # ---- 部品・パラメータ ----
    def nurse(self, n: int) -> str:
        names = self.I.nurse_names
        return names[n] if names is not None else f"N{n:02d}"

    def cat(self, k: str) -> str:
        return self.I.cat_names.get(k) or CAT_NAMES.get(k, k)

    def part(self, p) -> str:
        if isinstance(p, str):
            return p
        if isinstance(p, Nurse):
            return self.nurse(p.n)
        if isinstance(p, Day):
            return self.date(p.d)
        if isinstance(p, DaySpan):
            return f"{self.date(p.d0)} - {self.date(p.d1)}"
        if isinstance(p, Span):
            return f"{self.stamp(p.a)} - {self.stamp(p.b)}"
        if isinstance(p, Cat):
            return self.cat(p.k)
        if isinstance(p, Shift):
            return self.I.shifts[p.s].name
        if isinstance(p, Clock):
            return self.clock(p.slot)
        raise TypeError(f"メッセージの部品として使えない: {p!r}")

    def value(self, v) -> str:
        if isinstance(v, float):
            return f"{v:g}"
        if isinstance(v, (int, str)):
            return str(v)
        return self.part(v)

    def param(self, p: Param) -> str:
        return f"{p.name}{p.op}{self.value(p.value)}{p.unit}"

    # ---- メッセージ ----
    def message(self, target: tuple, params: tuple) -> str:
        """`<対象> <名前><演算子><値> …`。対象・パラメータは、あるものだけを空白で並べる"""
        return " ".join([self.part(p) for p in target] + [self.param(p) for p in params])
