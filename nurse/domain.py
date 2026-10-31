"""層2 病院・看護職共通の制約（診療報酬の施設基準など。病院の判断でオン・オフできる）。根拠は docs/legal/02_reimbursement.md。"""
from __future__ import annotations
import cpmpy as cp
from .kernel import ModelKernel
from .registry import constraint, Labeled, Aux
from .instance import inclusion_threshold, night_band
from .naming import nurse_name, day_name, slot_name, cat_name
from .preprocess import cover_spans, night_units


class DomainConstraints(ModelKernel):

    @constraint("offcount", "L3", scope="domain", title="公休数")
    def c_offcount(self):
        """公休数: Σ_d x[n,H+d,休] = F_n"""
        return [Labeled(cp.sum([self.x(n, self.t(d), self.OFF) for d in range(self.T)]) == self.I.off_days[n],
                        f"{nurse_name(n)}: 公休をちょうど{self.I.off_days[n]}日")
                for n in self.N]

    @constraint("cover", "L4", scope="domain", source=("REIMB-NIGHT-2",), title="時間帯ごとの必要人数")
    def c_cover(self):
        """区間人数: Σ_n Σ_{(δ,s)∈C_j} x[n,H+d+δ,s] ≥ r_j（基本区間＋支配除去後の j）"""
        out = []
        for d in range(self.T):
            for C, r, a, b in cover_spans(self.I, d):
                terms = [v for n in self.N for off, s in C
                         if (v := self.x(n, self.t(d) + off, s)) is not None]
                out.append(Labeled(cp.sum(terms) >= r,
                                   f"{day_name(self.I, d)} {slot_name(a)}〜{slot_name(b)}: 勤務中の看護師が{r}人以上"))
        return out

    @constraint("skill", "L4", scope="domain", title="夜勤リーダー資格者の配置")
    def c_skill(self):
        """リーダー: Σ_{n∈L} Σ_{s: k(s)=k} x[n,H+d,s] ≥ 1（k ∈ leader_cats）"""
        return [Labeled(cp.sum([v for n in self.I.seniors for v in self.cat(n, self.t(d), (k,))]) >= 1,
                        f"{day_name(self.I, d)}: {cat_name(k)}に夜勤リーダー資格者が1人以上")
                for d in range(self.T) for k in self.I.leader_cats]

    @constraint("72h", "L5", scope="domain", source=("REIMB-72H", "REIMB-NIGHT-WINDOW", "REIMB-72H-EXCLUDE"),
                title="月平均夜勤時間72時間以下")
    def c_72h(self):
        """月平均夜勤72h:  Σ_{n∈𝒩'} z_n (h_n − Λ) ≤ q,  h_n = Σ_{d,s} u_s x[n,H+d,s]
        𝒩' = 夜勤専従者を除く看護師。z_n ⇔ h_n ≥ θ_n（算入）。看護師ごとの項は _night_term が作る。"""
        I = self.I
        u = {s: night_units(I, s) for s in range(len(I.shifts))}
        out, hours, included = [], [], []
        for n in self.N:
            term = self._night_term(n, u, out)
            if term is not None:
                hours.append(term[0])
                included.append(term[1])
        if hours:
            out.append(Labeled(cp.sum(hours) <= I.limit_units * cp.sum(included) + I.budget_units,
                               f"病棟の月平均夜勤時間が{I.limit_units / 2:g}時間以下（算入者の平均。診療報酬の施設基準）"))
        return out

    def _night_hour_bounds(self, n: int, S: list[int], u: dict[int, int]) -> tuple[int, int]:
        """h_n の下界 LB_n・上界 UB_n（算入の事前判定に使う。解に対して常に成り立つ値でなければならない）。
        夜勤可能な看護師で night_band が有効なときだけ、夜勤回数の帯 [lo,hi] から導いた締まった界を使う。
        night_band がオフなら夜勤 0 回もありえるので、帯を前提にしない自明な界にする。"""
        T = self.T
        un = [u[s] for s in S if self.I.shifts[s].cat in self.I.night_cats]
        ud = [u[s] for s in S if self.I.shifts[s].cat not in self.I.night_cats] or [0]
        if not un:                                   # 夜勤できない: 夜勤以外の勤務だけ
            return 0, T * max(ud)
        if not self.is_active("night_band"):
            return 0, T * max(un + ud)
        lo, hi = night_band(self.I, self.eps)
        return lo * min(un), hi * max(un + ud) + (T - hi) * max(ud)

    def _night_term(self, n: int, u: dict[int, int], out: list):
        """看護師 n の (夜勤時間の式, 算入人数への寄与) を返す。算入されないなら None。
          UB_n < θ_n → 常に除外（z_n=0）  / LB_n ≥ θ_n → 常に算入（z_n=1 の定数）
          それ以外   → z_n と y[n,d,s] ≥ x[n,H+d,s] + z_n − 1（片側）を生成し、y で数える。
        生成した補助制約は out に足す。識別子 z_n→`z`, y[n,d,s]→`y`（解の読み出しに使わない）"""
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
