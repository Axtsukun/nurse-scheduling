"""層1 労働法規の制約（オフにできない）。根拠は docs/legal/01_law.md。"""
from __future__ import annotations
import cpmpy as cp
from cpmpy.expressions.core import Expression
from .kernel import ModelKernel
from .registry import constraint, Item, Aux, Constraints, ConstraintList
from .message import Nurse, DaySpan, Param
from .preprocess import holiday_windows, overnight_shifts
from .codes import InputError


class LawConstraints(ModelKernel):
    """層1（law）の制約。労働法規に由来し、オフにできない。`ModelKernel` を継承したミックスイン。"""

    @constraint("holiday", "L2", scope="law", source=("LAW-HOLIDAY",), title="休日数下限")
    def c_holiday(self) -> Constraints:
        """労基法35条: 各窓 [t0,t1) に休日が k 日以上（窓は holiday_windows）。
        休日 = 「休」の日（有休は労働日なので数えない）。休日の指標は _holiday_flag を参照。

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        out: ConstraintList = []
        windows = holiday_windows(self.I)
        for n in self.N:
            if self.x(n, self.t(0), self.OFF) is None:
                raise InputError(f"看護師 {n}: 休がドメインにないため休日を与えられない")
            flags: dict[int, object] = {}                      # 時点 t → 休日の指標（窓をまたいで使い回す）
            for t0, t1, k in windows:
                for t in range(t0, t1):
                    if t not in flags:
                        flags[t] = self._holiday_flag(n, t, out)
                out.append(Item(cp.sum([flags[t] for t in range(t0, t1)]) >= k,
                                (Nurse(n), DaySpan(t0 - self.H, t1 - 1 - self.H)), (Param("休日数", k),)))
        return out

    def _holiday_flag(self, n: int, t: int, side_constraints: ConstraintList) -> Expression:
        """時点 t が看護師 n の休日である指標（0/1 の式または Boolean）。
        既定は x[n,t,休]。holiday_strict_calendar なら、前日の勤務が日をまたぐ日は休日にしない:
        hol ≤ x[n,t,休], hol ≤ 1 − Σ_{s: 翌日にかかる} x[n,t−1,s]（片側定義。補助制約は side_constraints に足す）。

        Args:
            n: 看護師の添字。
            t: 時点。
            side_constraints: 補助制約（休日の指標の定義式）を足すリスト。

        Returns:
            休日の指標（0/1 の式または Boolean）。"""
        off = self.x(n, t, self.OFF)
        if not self.I.rules.law.holiday_strict_calendar or t == 0:
            return off
        prev = self.xs(n, t - 1, overnight_shifts(self.I))
        if not prev:
            return off
        hol = cp.boolvar(name=f"hol_{n}_{t}")
        side_constraints.extend([Aux(hol <= off), Aux(hol <= 1 - cp.sum(prev))])
        return hol
