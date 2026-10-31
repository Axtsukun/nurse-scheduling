"""日本語の表示名。診断の説明文（制約のラベル）で、看護師・日・時刻・勤務を人が読める形にする。"""
from __future__ import annotations
from .instance import Instance

WEEKDAYS = "月火水木金土日"
CAT_NAMES = {"D": "日勤", "E": "準夜", "N": "深夜", "P": "パート", "OFF": "公休", "LV": "有休"}   # ShiftDef.cat → 表示名


def nurse_name(n: int) -> str:
    """看護師 n の表示名（勤務表の行と同じ N05 の形）"""
    return f"N{n:02d}"


def day_name(I: Instance, d: int) -> str:
    """計画日 d の表示名。例: 6日目(土)"""
    return f"{d + 1}日目({WEEKDAYS[(I.start_weekday + d) % 7]})"


def time_name(I: Instance, t: int) -> str:
    """時点 t（t = H + 計画日 d。t < H は履歴）の表示名"""
    H = I.n_history
    return day_name(I, t - H) if t >= H else f"前月末{H - t}日前"


def slot_name(h: int) -> str:
    """30分スロット → 時刻。例: 17 → 8:30"""
    return f"{h // 2}:{30 * (h % 2):02d}"


def shift_name(I: Instance, s: int) -> str:
    return I.shifts[s].name


def cat_name(cat: str) -> str:
    return CAT_NAMES.get(cat, cat)
