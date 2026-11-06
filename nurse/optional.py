"""層3 努力義務・ガイドライン・個別要望の制約（クライアントが選ぶ）。根拠は docs/legal/03_guidelines.md。"""
from __future__ import annotations
import math
import cpmpy as cp
from .kernel import ModelKernel
from .registry import constraint, Item, Aux, Constraints, ConstraintList
from .instance import DAY, is_part, night_band, night_capable, weekend_days, weekend_pairs
from .message import Nurse, Day, DaySpan, Clock, Param
from .preprocess import rest_cliques, symmetry_classes


class OptionalConstraints(ModelKernel):

    # ---- 系列（ガイドライン） ----
    """層3（optional）の制約。努力義務・ガイドライン・個別要望。`Rules.off` でオフにできる。`ModelKernel` を継承したミックスイン。"""
    @constraint("rest", "L2", scope="optional", source=("GL-INTERVAL-LAW", "GL-REST-11H"), title="勤務間隔下限")
    def c_rest(self) -> Constraints:
        """勤務間隔 ρ（閾値分解）: Σ_{a∈A_θ} x[n,t,a] + Σ_{b∈B_θ} x[n,t+1,b] ≤ 1

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        out, cl = [], rest_cliques(self.I)
        for n in self.N:
            for t in range(self.H - 1, self.H + self.T - 1):
                for A, B in cl:
                    lhs, rhs = self.xs(n, t, A), self.xs(n, t + 1, B)
                    if lhs and rhs:
                        out.append(Item(cp.sum(lhs) + cp.sum(rhs) <= 1,
                                        (Nurse(n), DaySpan(t - self.H, t + 1 - self.H)), self._rest_params(A)))
        return out

    def _rest_params(self, A: frozenset) -> tuple:
        """勤務間隔の制約（閾値 θ の組 A_θ, B_θ）のパラメータ。θ = A_θ の最も早い終業時刻なので、組ごとに値が変わる:
        前日の終業が θ 以降の勤務の次は、翌日の始業が θ + ρ − 48 より前の勤務に就けない

        Args:
            A: 閾値 θ の組の A_θ（勤務の添字の集合）。

        Returns:
            `Param` の組（勤務間隔・前日終業・翌日始業）。"""
        I = self.I
        theta = min(I.shifts[a].end for a in A)
        return (Param("勤務間隔", I.min_rest / 2, "h"),
                Param("前日終業", Clock(theta), op=">="),
                Param("翌日始業", Clock(theta + I.min_rest - DAY), op="<"))

    @constraint("consec", "L2", scope="optional", source=("GL-CONSEC-WORK",), title="連続勤務日数上限")
    def c_consec(self) -> Constraints:
        """連続勤務 ≤ K: Σ_{u=t}^{t+K} w[n,u] ≤ K（計画日を含む窓）

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        K = self.I.max_consec_work
        out: ConstraintList = []
        for n in self.N:
            w = self.wvec(n)
            out += [Item(cp.sum(w[t:t + K + 1]) <= K,
                         (Nurse(n), DaySpan(t - self.H, t + K - self.H)), (Param("連続勤務日数", K),))
                    for t in range(self.H + self.T - K) if t + K >= self.H]
        return out

    @constraint("offblock", "L2", scope="optional", title="休み連続日数下限")
    def c_offblock(self) -> Constraints:
        """非勤務ブロック長 ≥ m: w[n,t−1] + Σ_{u=t}^{t+j−1}(1−w[n,u]) + w[n,t+j] ≤ j+1（j<m）

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        m, out = self.I.min_off_block, []
        for n in self.N:
            w = self.wvec(n)
            for j in range(1, m):
                for t in range(1, self.H + self.T - j):
                    if t + j - 1 >= self.H:
                        out.append(Item(w[t - 1] + cp.sum(1 - w[t:t + j]) + w[t + j] <= j + 1,
                                        (Nurse(n), Day(t - self.H)), (Param("休み連続日数", m), Param("ブロック長", j))))
        return out

    # ---- 個人の回数・週末 ----
    @constraint("weekend", "L3", scope="optional", source=("GL-WEEKEND",), title="勤務週末数上限")
    def c_weekend(self) -> Constraints:
        """勤務週末数 ≤ W。週末 = (土曜, 日曜) の組（weekend_pairs）。
        補助 wk[n,d] = w[n,土] ∨ w[n,日]（識別子 wk。両側で定義し、和が数えられるようにする）

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        out: ConstraintList = []
        for n in self.N:
            wks = []
            for sat, sun in weekend_pairs(self.I):
                a, b = self.work(n, self.t(sat)), self.work(n, self.t(sun))
                wk = cp.intvar(0, 1, name=f"wk_{n}_{sat}")
                out += [Aux(wk >= a), Aux(wk >= b), Aux(wk <= a + b)]
                wks.append(wk)
            if wks:
                W = self.I.max_work_weekends
                out.append(Item(cp.sum(wks) <= W, (Nurse(n),), (Param("勤務週末数", W),)))
        return out

    # ---- ε制約 ----
    @constraint("night_band", "EPS", scope="optional", source=("GL-NIGHT-COUNT",), title="夜勤回数範囲")
    def c_night_band(self) -> Constraints:
        """⌊avg⌋−δ ≤ c[n] ≤ ⌈avg⌉+δ（c[n] は夜勤回数。夜勤可能・非専従者）。
        注意: 72h の算入判定の界はこの帯を前提にする（domain._night_hour_bounds が is_active で確認）。

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        lo, hi = night_band(self.I, self.eps)
        out: ConstraintList = []
        for n in self.N:
            if not night_capable(self.I, n) or n in self.I.night_only:
                continue
            c = self.night_count(n)
            out += [Item(c >= lo, (Nurse(n),), (Param("夜勤回数下限", lo),)),
                    Item(c <= hi, (Nurse(n),), (Param("夜勤回数上限", hi),))]
        return out

    @constraint("weekend_spread", "EPS", scope="optional", title="土日勤務日数差上限")
    def c_weekend_spread(self) -> Constraints:
        """土日勤務日数 e_n = Σ_{d∈土日} w[n,H+d] について、区分 g(正規/パート)ごとに
        max_n e_n − min_n e_n ≤ Δ_g ⇔ ある窓下端 m_g があって m_g ≤ e_n ≤ m_g + Δ_g（n ∈ g）。
        m_g = Σ_k k·p[g,k]（p[g,·] は one-hot の 0/1 変数（intvar(0, 1)））。
        制約本数は全ペア方式の O(|g|²) に対し O(|g|)。識別子 p[g,k] → `p`

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        out: ConstraintList
        I, out = self.I, []
        sat, sun = weekend_days(I)
        E = len(sat) + len(sun)
        for flag, delta in ((False, self.eps.weekend_spread_regular), (True, self.eps.weekend_spread_part)):
            g = [n for n in self.N if is_part(I, n) == flag]
            if len(g) < 2:
                continue
            p = cp.intvar(0, 1, shape=E + 1, name=f"wsp_{int(flag)}")
            m = cp.sum([k * p[k] for k in range(E + 1)])
            out.append(Aux(cp.sum(p) == 1))
            who = "パート" if flag else "正規"
            for n in g:
                e = cp.sum([self.work(n, self.t(d)) for d in sat + sun])
                target, params = (who, Nurse(n)), (Param("土日勤務日数差", delta),)       # 下限側・上限側の2本は同じメッセージ（ID で区別）
                out += [Item(e >= m, target, params), Item(e <= m + delta, target, params)]
        return out

    @constraint("sym_break", "SYM", scope="optional", title="夜勤回数整列")
    def c_sym_break(self) -> Constraints:
        """同種の看護師 n_1 < n_2 < … の夜勤回数を非増加に並べる: c[n_i] ≥ c[n_{i+1}]。
        夜勤できない集団（パート等）は対象外。eps.symmetry_break == "" なら制約なし。
        "exact" のみ健全、"relaxed" は不完全（symmetry_classes 参照。実験用）

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        if not self.eps.symmetry_break:
            return []
        out: ConstraintList = []
        for g in symmetry_classes(self.I, self.eps.symmetry_break):
            g = [n for n in g if night_capable(self.I, n) and n not in self.I.night_only]
            cs = [self.night_count(n) for n in g]
            out += [Item(cs[i] >= cs[i + 1], (Nurse(g[i]),), (Param("夜勤回数", Nurse(g[i + 1]), op=">="),))
                    for i in range(len(g) - 1)]
        return out

    # ---- 個別要望（ε制約） ----
    @constraint("req_off", "EPS", scope="optional", title="休み要望違反数上限")
    def c_req_off(self) -> Constraints:
        """休み要望（希望休を含む）: Σ_{(n,d)∈Q^off} (1 − x[n,H+d,休]) ≤ ⌊ρ·|Q^off|⌋（ρ = eps.req_viol_ratio）。
        勤務者からの「この日は休みたい」という要望。守れなくてもよいが、違反は要望数の割合 ρ まで

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        cells = [(n, d) for n, ds in self.I.req_off.items() for d in ds]
        if not cells:
            return []
        cap = math.floor(self.eps.req_viol_ratio * len(cells))
        return [Item(cp.sum([1 - self.x_taken(n, self.t(d), self.OFF) for n, d in cells]) <= cap,
                     (), (Param("要望数", len(cells)), Param("違反数", cap)))]

    @constraint("req_shift", "EPS", scope="optional", title="シフト要望違反数上限")
    def c_req_shift(self) -> Constraints:
        """シフト要望: Σ_{(n,d,s)∈Q^shift} (1 − x[n,H+d,s]) ≤ ⌊ρ·|Q^shift|⌋

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        cells = [(n, d, s) for n, ds in self.I.req_shift.items() for d, s in ds]
        if not cells:
            return []
        cap = math.floor(self.eps.req_viol_ratio * len(cells))
        return [Item(cp.sum([1 - self.x_taken(n, self.t(d), s) for n, d, s in cells]) <= cap,
                     (), (Param("要望数", len(cells)), Param("違反数", cap)))]
