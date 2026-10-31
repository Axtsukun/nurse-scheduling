"""層1 労働法規の制約（オフにできない）。根拠は docs/legal/01_law.md。"""
from __future__ import annotations
import cpmpy as cp
from .kernel import ModelKernel
from .registry import constraint, Labeled, Aux
from .naming import nurse_name, time_name
from .instance import HOLIDAY_WEEKLY
from .preprocess import holiday_windows, overnight_shifts


class LawConstraints(ModelKernel):

    @constraint("holiday", "L2", scope="law", source=("LAW-HOLIDAY",), title="休日の付与（労基法35条）")
    def c_holiday(self):
        """労基法35条: 各窓 [t0,t1) に休日が k 日以上（窓は holiday_windows）。
        休日 = 「休」の日（有休は労働日なので数えない）。休日の指標は _holiday_flag を参照。"""
        out = []
        windows = holiday_windows(self.I)
        for n in self.N:
            if self.x(n, self.t(0), self.OFF) is None:
                raise ValueError(f"看護師 {n}: 休がドメインにないため休日を与えられない")
            flags: dict[int, object] = {}                      # 時点 t → 休日の指標（窓をまたいで使い回す）
            for t0, t1, k in windows:
                for t in range(t0, t1):
                    if t not in flags:
                        flags[t] = self._holiday_flag(n, t, out)
                span = "毎週1回以上" if self.I.rules.law.holiday_rule == HOLIDAY_WEEKLY else "4週間に4日以上"
                out.append(Labeled(cp.sum([flags[t] for t in range(t0, t1)]) >= k,
                                   f"{nurse_name(n)}: {time_name(self.I, t0)}〜{time_name(self.I, t1 - 1)}に"
                                   f"休日が{k}日以上（{span}の休日。労基法35条）"))
        return out

    def _holiday_flag(self, n: int, t: int, side_constraints: list):
        """時点 t が看護師 n の休日である指標（0/1 の式または Boolean）。
        既定は x[n,t,休]。holiday_strict_calendar なら、前日の勤務が日をまたぐ日は休日にしない:
        hol ≤ x[n,t,休], hol ≤ 1 − Σ_{s: 翌日にかかる} x[n,t−1,s]（片側定義。補助制約は side_constraints に足す）。"""
        off = self.x(n, t, self.OFF)
        if not self.I.rules.law.holiday_strict_calendar or t == 0:
            return off
        prev = self.xs(n, t - 1, overnight_shifts(self.I))
        if not prev:
            return off
        hol = cp.boolvar(name=f"hol_{n}_{t}")
        side_constraints.extend([Aux(hol <= off), Aux(hol <= 1 - cp.sum(prev))])
        return hol
