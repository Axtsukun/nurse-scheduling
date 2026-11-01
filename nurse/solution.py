"""解（割当表）の読み取り用の部品。検証器と評価が共通に使う。モデルの式には依存しない。

  `A[n][d]` = 計画日 d に看護師 n が就く勤務の添字 s（𝒮 の添字）
"""
from __future__ import annotations
from .instance import Instance, DAY

Assignment = list[list[int]]


def full_sequences(I: Instance, A: Assignment) -> list[list[int]]:
    """履歴（前月末）を前置した勤務列。時点 t = H + 計画日 d"""
    return [[int(v) for v in I.history[n]] + A[n] for n in range(I.n_nurses)]


def staff_on_duty(I: Instance, full: list[list[int]], d: int, slot: int) -> int:
    """日 d の 30分スロット slot に勤務中の人数（前日から日をまたぐ勤務を含む。式を使わず直接数える）"""
    H, cnt = I.n_history, 0
    for seq in full:
        for off in (-1, 0):
            sd = I.shifts[seq[H + d + off]]
            if sd.is_work and sd.start + off * DAY <= slot < sd.end + off * DAY:
                cnt += 1
    return cnt
