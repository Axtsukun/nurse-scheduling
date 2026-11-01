"""合成インスタンス（三交代 ±30分 ＋ パート定型）と、個別要望の付与。実データの代わりに計測・テストで使う。"""
from __future__ import annotations
import dataclasses
import random
import numpy as np
from .instance import Instance, ShiftDef, DAY, OFF_NAME

# 看護師数 → (常勤, パート)。比は 26:4。需要・資格者数は常勤数に比例させる（synthetic の f）
SIZES = {30: (26, 4), 60: (52, 8), 90: (78, 12)}
BASE_FULL = 26                        # 既定規模の常勤数。需要・資格者数の基準
WORK_DAYS_OFF_FULL, OFF_DAYS_PART = 9, 14      # 公休数: 常勤は月9日、パートは月14日


def hm(h: int, m: int = 0) -> int:
    """時刻 h 時 m 分 → 30分スロット数（m は 0 か 30）

    Args:
        h: 時。
        m: 分（0 か 30）。

    Returns:
        30分スロット数。"""
    return h * 2 + m // 30


def _shift_catalog() -> list[ShiftDef]:
    """勤務の定義。休・有休、常勤の日/準/深（各 −30分/標準/+30分、拘束8.5h）、パートの4h系・6h系

    Returns:
        勤務の定義のリスト。"""
    S = [ShiftDef(OFF_NAME, 0, 0, "OFF", False), ShiftDef("有休", 0, 0, "LV", False, fixed_only=True)]
    for nm, cat, st in (("日", "D", hm(8, 30)), ("準", "E", hm(16, 30)), ("深", "N", hm(0, 30))):
        for dv, tag in ((-1, "早"), (0, ""), (1, "遅")):
            S.append(ShiftDef(nm + tag, st + dv, st + dv + 17, cat, True))       # 拘束8.5h
    for nm, st, length in (("P4a", hm(8, 30), 8), ("P4b", hm(9), 8), ("P4c", hm(13), 8), ("P4d", hm(13, 30), 8),
                           ("P6a", hm(8, 30), 12), ("P6b", hm(9), 12), ("P6c", hm(10), 12)):
        S.append(ShiftDef(nm, st, st + length, "P", True))
    return S


def _history(S: list[ShiftDef], n_full: int, n_part: int) -> np.ndarray:
    """前月末5日の勤務。常勤は 準,休,深,日,日 の巡回（隣接しても勤務間隔11hを満たす並び）、パートは P4a と休の交互

    Args:
        S: 勤務の定義のリスト。
        n_full: 常勤の人数。
        n_part: パートの人数。

    Returns:
        履歴の配列（看護師 × 5日。値は勤務の添字）。"""
    idx = {s.name: i for i, s in enumerate(S)}
    tmpl = ["準", OFF_NAME, "深", "日", "日"]
    full = [[idx[tmpl[(i + k) % 5]] for k in range(5)] for i in range(n_full)]
    part = [[idx[["P4a", OFF_NAME][(i + k) % 2]] for k in range(5)] for i in range(n_part)]
    return np.array(full + part)


def _fixed_and_soft(rng: random.Random, S: list[ShiftDef], n: int, T: int):
    """確定休（各人2日）・有休（5人に1人が1日）と、希望休（各人2日）を乱数で作る。
    乱数の消費順は結果の再現性に影響するため、変更しない（seed ごとに同じインスタンスになる）

    Args:
        rng: 乱数生成器。
        S: 勤務の定義のリスト。
        n: 看護師数。
        T: 計画日数。"""
    idx = {s.name: i for i, s in enumerate(S)}
    fixed = {}
    for i in range(n):
        for d in rng.sample(range(T), 2):
            fixed[(i, d)] = idx[OFF_NAME]
        d = rng.randrange(T)
        if i % 5 == 0 and (i, d) not in fixed:
            fixed[(i, d)] = idx["有休"]
    soft = {i: rng.sample([d for d in range(T) if (i, d) not in fixed], 2) for i in range(n)}
    return fixed, soft


def synthetic(n_full: int = 26, n_part: int = 4, T: int = 30, seed: int = 0, start_date=None) -> Instance:
    """合成インスタンス。n_full/n_part は常勤・パートの人数、T は計画日数、seed は確定休・希望休の乱数。
    start_date は計画日0の日付（月曜日であること。診断のメッセージを MM/DD 表記にする。未指定なら N日目(曜)）。
    需要は平日 [0:00-8:30) 3人・[8:30-17:00) 10人・[17:00-24:00) 3人（土日は日中6人）を常勤数に比例して拡大する。

    Args:
        n_full: 常勤の人数。
        n_part: パートの人数。
        T: 計画日数。
        seed: 乱数の種。
        start_date: 計画日0の日付（月曜日）。

    Returns:
        合成した入力データ。"""
    rng = random.Random(seed)
    S = _shift_catalog()
    full = [i for i, s in enumerate(S) if s.cat in ("OFF", "D", "E", "N")]
    part = [i for i, s in enumerate(S) if s.cat in ("OFF", "P")]
    n = n_full + n_part
    f = n_full / BASE_FULL
    R = lambda r: max(1, round(r * f))
    demand = [[(0, hm(8, 30), R(3)), (hm(8, 30), hm(17), R(6 if d % 7 in (5, 6) else 10)), (hm(17), DAY, R(3))]
              for d in range(T)]
    fixed, soft = _fixed_and_soft(rng, S, n, T)
    return Instance(S, [full] * n_full + [part] * n_part, T, 0, _history(S, n_full, n_part), demand,
                    seniors=list(range(round(10 * f))), leader_cats=("E", "N"), night_cats=("E", "N"),
                    night_avg=2 * R(3) * T / n_full,
                    off_days=[WORK_DAYS_OFF_FULL] * n_full + [OFF_DAYS_PART] * n_part,
                    fixed=fixed, soft_off=soft, start_date=start_date)


def synthetic_for(n_nurses: int, T: int = 30, seed: int = 0) -> Instance:
    """看護師数（SIZES のキー: 30/60/90）で指定する合成インスタンス

    Args:
        n_nurses: 看護師数（30 / 60 / 90）。
        T: 計画日数。
        seed: 乱数の種。

    Returns:
        合成した入力データ。"""
    n_full, n_part = SIZES[n_nurses]
    return synthetic(n_full, n_part, T=T, seed=seed)


def with_requests(I: Instance, rate: float = 0.10, seed: int = 0) -> Instance:
    """個別要望を加えた Instance を返す（元は変更しない）。
    新規の要望セル数 = rate × (看護師数 × 日数)。内訳は 休み要望 : シフト要望 = 1 : 1。
    対象セルは確定割当・既存の希望休と重ならない (n,d) から非復元抽出。
    シフト要望の値は、その看護師が取れる勤務（有休など固定専用を除く）から一様に選ぶ。既存の soft_off は残す。

    Args:
        I: 元の入力データ（変更しない）。
        rate: 要望セル数の割合（看護師数 × 日数に対する）。
        seed: 乱数の種。

    Returns:
        要望を加えた新しい入力データ。"""
    rng = random.Random(seed)
    pool = [(n, d) for n in range(I.n_nurses) for d in range(I.n_days)
            if (n, d) not in I.fixed and d not in I.soft_off.get(n, [])]
    k = round(rate * I.n_nurses * I.n_days)
    k_off = k // 2
    cells = rng.sample(pool, k)
    req_off: dict[int, list[int]] = {}
    req_shift: dict[int, list[tuple[int, int]]] = {}
    for n, d in cells[:k_off]:
        req_off.setdefault(n, []).append(d)
    for n, d in cells[k_off:]:
        work = [s for s in I.domain[n] if I.shifts[s].is_work and not I.shifts[s].fixed_only]
        req_shift.setdefault(n, []).append((d, rng.choice(work)))
    return dataclasses.replace(I, req_off=req_off, req_shift=req_shift)
