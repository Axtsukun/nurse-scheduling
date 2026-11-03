"""層2 病院・看護職共通の制約（診療報酬の施設基準など。病院の判断でオン・オフできる）。根拠は docs/legal/02_reimbursement.md。"""
from __future__ import annotations
import cpmpy as cp
from cpmpy.expressions.core import Expression
from .kernel import ModelKernel
from .registry import constraint, Item, Aux, Constraints, ConstraintList
from .instance import DAY, inclusion_threshold, night_band
from .message import Nurse, Day, Span, Cat, Param
from .preprocess import cover_spans, night_units


class DomainConstraints(ModelKernel):
    """層2（domain）の制約。病院・看護職共通（診療報酬の施設基準など）。`Rules.off` でオフにできる。`ModelKernel` を継承したミックスイン。"""

    @constraint("offcount", "L3", scope="domain", title="公休数一致")
    def c_offcount(self) -> Constraints:
        """公休数: Σ_d x[n,H+d,休] = F_n

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        return [Item(cp.sum([self.x(n, self.t(d), self.OFF) for d in range(self.T)]) == self.I.off_days[n],
                     (Nurse(n),), (Param("公休数", self.I.off_days[n]),))
                for n in self.N]

    @constraint("cover", "L4", scope="domain", source=("REIMB-NIGHT-2",), title="人数制約下限")
    def c_cover(self) -> Constraints:
        """区間人数: Σ_n Σ_{(δ,s)∈C_j} x[n,H+d+δ,s] ≥ r_j（基本区間＋支配除去後の j）

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        out: ConstraintList = []
        for d in range(self.T):
            for C, r, a, b in cover_spans(self.I, d):
                terms = [v for n in self.N for off, s in C
                         if (v := self.x(n, self.t(d) + off, s)) is not None]
                out.append(Item(cp.sum(terms) >= r, (Span(d * DAY + a, d * DAY + b),), (Param("勤務者数", r),)))
        return out

    @constraint("skill", "L4", scope="domain", title="リーダー数下限")
    def c_skill(self) -> Constraints:
        """リーダー: Σ_{n∈L} Σ_{s: k(s)=k} x[n,H+d,s] ≥ 1（k ∈ leader_cats）

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        return [Item(cp.sum([v for n in self.I.seniors for v in self.cat(n, self.t(d), (k,))]) >= 1,
                     (Day(d), Cat(k)), (Param("リーダー数", 1),))
                for d in range(self.T) for k in self.I.leader_cats]

    @constraint("72h", "L5", scope="domain", source=("REIMB-72H", "REIMB-NIGHT-WINDOW", "REIMB-72H-EXCLUDE"),
                title="夜勤時間平均上限")
    def c_72h(self) -> Constraints:
        """月平均夜勤72h:  Σ_{n∈𝒩'} z_n (h_n − Λ) ≤ q,  h_n = Σ_{d,s} u_s x[n,H+d,s]
        𝒩' = 夜勤専従者を除く看護師。z_n ⇔ h_n ≥ θ_n（算入）。看護師ごとの項は _night_term が作る。

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        I = self.I
        u = {s: night_units(I, s) for s in range(len(I.shifts))}
        out: ConstraintList
        out, hours, included = [], [], []
        for n in self.N:
            term = self._night_term(n, u, out)
            if term is not None:
                hours.append(term[0])
                included.append(term[1])
        if hours:
            out.append(Item(cp.sum(hours) <= I.limit_units * cp.sum(included) + I.budget_units,
                            ("病棟",), (Param("月平均夜勤時間", I.limit_units / 2, "h"),)))
        return out

    def _night_hour_bounds(self, n: int, S: list[int], u: dict[int, int]) -> tuple[int, int]:
        """h_n の下界 LB_n・上界 UB_n（算入の事前判定に使う。解に対して常に成り立つ値でなければならない）。
        夜勤可能な看護師で night_band が有効なときだけ、夜勤回数の帯 [lo,hi] から導いた締まった界を使う。
        night_band がオフなら夜勤 0 回もありえるので、帯を前提にしない自明な界にする。

        Args:
            n: 看護師の添字。
            S: その看護師が取れる、夜勤時間が正の勤務の添字のリスト。
            u: 勤務の添字 → 夜勤時間のスロット数。

        Returns:
            `(下界 LB_n, 上界 UB_n)`。"""
        T = self.T
        un = [u[s] for s in S if self.I.shifts[s].cat in self.I.night_cats]
        ud = [u[s] for s in S if self.I.shifts[s].cat not in self.I.night_cats] or [0]
        if not un:                                   # 夜勤できない: 夜勤以外の勤務だけ
            return 0, T * max(ud)
        if not self.is_active("night_band"):
            return 0, T * max(un + ud)
        lo, hi = night_band(self.I, self.eps)
        return lo * min(un), hi * max(un + ud) + (T - hi) * max(ud)

    def _night_term(self, n: int, u: dict[int, int], out: ConstraintList) -> tuple[Expression, Expression | int] | None:
        """看護師 n の (夜勤時間の式, 算入人数への寄与) を返す。算入されないなら None。
          UB_n < θ_n → 常に除外（z_n=0）  / LB_n ≥ θ_n → 常に算入（z_n=1 の定数）
          それ以外   → z_n と y[n,d,s] ≥ x[n,H+d,s] + z_n − 1（片側）を生成し、y で数える。
        生成した補助制約は out に足す。識別子 z_n→`z`, y[n,d,s]→`y`（解の読み出しに使わない）

        Args:
            n: 看護師の添字。
            u: 勤務の添字 → 夜勤時間のスロット数。
            out: 補助制約（定義式）を足すリスト。

        Returns:
            `(夜勤時間の式, 算入人数への寄与)`。算入されないなら `None`。"""
        I, T = self.I, self.T
        if n in I.night_only:
            return None
        S = [s for s in self.dom[n] if u[s] > 0]
        if not S:
            return None
        theta = inclusion_threshold(I, n)
        LB, UB = self._night_hour_bounds(n, S, u)
        if UB < theta:
            return None
        h = cp.sum([u[s] * v for d in range(T) for s in S
                    if (v := self.x(n, self.t(d), s)) is not None])
        if LB >= theta:
            return h, 1
        z = cp.boolvar(name=f"z_{n}")
        out += [Aux(h >= theta * z), Aux(h <= theta - 1 + (UB - theta + 1) * z)]
        ys = []
        for d in range(T):
            for s in S:
                y = cp.boolvar(name=f"y_{n}_{d}_{s}")
                out.append(Aux(y >= self.x(n, self.t(d), s) + z - 1))
                ys.append(u[s] * y)
        return cp.sum(ys), z
