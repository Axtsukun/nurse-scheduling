"""層3 努力義務・ガイドライン・個別要望の制約（クライアントが選ぶ）。根拠は docs/legal/03_guidelines.md。"""
from __future__ import annotations
import math
import cpmpy as cp
from .kernel import ModelKernel
from .registry import constraint, Labeled, Aux
from .instance import (is_part, night_band, night_capable, weekend_days, weekend_pairs)
from .naming import nurse_name, time_name
from .preprocess import rest_cliques, symmetry_classes


class OptionalConstraints(ModelKernel):

    # ---- 系列（ガイドライン） ----
    @constraint("rest", "L2", scope="optional", source=("GL-INTERVAL-LAW", "GL-REST-11H"), title="勤務間隔の下限")
    def c_rest(self):
        """勤務間隔 ρ（閾値分解）: Σ_{a∈A_θ} x[n,t,a] + Σ_{b∈B_θ} x[n,t+1,b] ≤ 1"""
        out, cl = [], rest_cliques(self.I)
        for n in self.N:
            for t in range(self.H - 1, self.H + self.T - 1):
                for A, B in cl:
                    lhs, rhs = self.xs(n, t, A), self.xs(n, t + 1, B)
                    if lhs and rhs:
                        out.append(Labeled(cp.sum(lhs) + cp.sum(rhs) <= 1,
                                           f"{nurse_name(n)}: {time_name(self.I, t)}→{time_name(self.I, t + 1)}の"
                                           f"勤務間隔が{self.I.min_rest / 2:g}時間以上"))
        return out

    @constraint("consec", "L2", scope="optional", source=("GL-CONSEC-WORK",), title="連続勤務日数の上限")
    def c_consec(self):
        """連続勤務 ≤ K: Σ_{u=t}^{t+K} w[n,u] ≤ K（計画日を含む窓）"""
        K = self.I.max_consec_work
        out = []
        for n in self.N:
            w = self.wvec(n)
            out += [Labeled(cp.sum(w[t:t + K + 1]) <= K,
                            f"{nurse_name(n)}: {time_name(self.I, t)}〜{time_name(self.I, t + K)}の{K + 1}日間に勤務は{K}日以内"
                            f"（連続勤務{K}日まで）")
                    for t in range(self.H + self.T - K) if t + K >= self.H]
        return out

    @constraint("offblock", "L2", scope="optional", title="休みの連続日数の下限")
    def c_offblock(self):
        """非勤務ブロック長 ≥ m: w[n,t−1] + Σ_{u=t}^{t+j−1}(1−w[n,u]) + w[n,t+j] ≤ j+1（j<m）"""
        m, out = self.I.min_off_block, []
        for n in self.N:
            w = self.wvec(n)
            for j in range(1, m):
                for t in range(1, self.H + self.T - j):
                    if t + j - 1 >= self.H:
                        out.append(Labeled(w[t - 1] + cp.sum(1 - w[t:t + j]) + w[t + j] <= j + 1,
                                           f"{nurse_name(n)}: {time_name(self.I, t)}からの{j}日間だけの休みを作らない"
                                           f"（休みは{m}日以上続ける）"))
        return out

    # ---- 個人の回数・週末 ----
    @constraint("weekend", "L3", scope="optional", source=("GL-WEEKEND",), title="勤務する週末の回数")
    def c_weekend(self):
        """勤務週末数 ≤ W。週末 = (土曜, 日曜) の組（weekend_pairs）。
        補助 wk[n,d] = w[n,土] ∨ w[n,日]（識別子 wk。両側で定義し、和が数えられるようにする）"""
        out = []
        for n in self.N:
            wks = []
            for sat, sun in weekend_pairs(self.I):
                a, b = self.work(n, self.t(sat)), self.work(n, self.t(sun))
                wk = cp.boolvar(name=f"wk_{n}_{sat}")
                out += [Aux(wk >= a), Aux(wk >= b), Aux(wk <= a + b)]
                wks.append(wk)
            if wks:
                W = self.I.max_work_weekends
                out.append(Labeled(cp.sum(wks) <= W,
                                   f"{nurse_name(n)}: 勤務する週末は{W}回以内（土日のどちらかに勤務すれば1回）"))
        return out

    # ---- ε制約 ----
    @constraint("night_band", "EPS", scope="optional", source=("GL-NIGHT-COUNT",), title="夜勤回数の帯")
    def c_night_band(self):
        """⌊avg⌋−δ ≤ c[n] ≤ ⌈avg⌉+δ（c[n] は夜勤回数。夜勤可能・非専従者）。
        注意: 72h の算入判定の界はこの帯を前提にする（domain._night_hour_bounds が is_active で確認）。"""
        lo, hi = night_band(self.I, self.eps)
        out = []
        for n in self.N:
            if not night_capable(self.I, n) or n in self.I.night_only:
                continue
            c = self.night_count(n)
            out += [Labeled(c >= lo, f"{nurse_name(n)}: 夜勤回数が{lo}回以上"),
                    Labeled(c <= hi, f"{nurse_name(n)}: 夜勤回数が{hi}回以下")]
        return out

    @constraint("weekend_spread", "EPS", scope="optional", title="土日勤務日数のばらつき")
    def c_weekend_spread(self):
        """土日勤務日数 e_n = Σ_{d∈土日} w[n,H+d] について、区分 g(正規/パート)ごとに
        max_n e_n − min_n e_n ≤ Δ_g ⇔ ある窓下端 m_g があって m_g ≤ e_n ≤ m_g + Δ_g（n ∈ g）。
        m_g = Σ_k k·p[g,k]（p[g,·] は one-hot の Boolean。cp.intvar は使わない）。
        制約本数は全ペア方式の O(|g|²) に対し O(|g|)。識別子 p[g,k] → `p`"""
        I, out = self.I, []
        sat, sun = weekend_days(I)
        E = len(sat) + len(sun)
        for flag, delta in ((False, self.eps.weekend_spread_regular), (True, self.eps.weekend_spread_part)):
            g = [n for n in self.N if is_part(I, n) == flag]
            if len(g) < 2:
                continue
            p = cp.boolvar(shape=E + 1, name=f"wsp_{int(flag)}")
            m = cp.sum([k * p[k] for k in range(E + 1)])
            out.append(Aux(cp.sum(p) == 1))
            who = "パート" if flag else "正規"
            for n in g:
                e = cp.sum([self.work(n, self.t(d)) for d in sat + sun])
                note = f"{who}職員の土日勤務日数の差は{delta}日以内"
                out += [Labeled(e >= m, f"{nurse_name(n)}: {note}（区分内の最小側）"),
                        Labeled(e <= m + delta, f"{nurse_name(n)}: {note}（区分内の最大側）")]
        return out

    @constraint("sym_break", "SYM", scope="optional", title="同種の看護師の夜勤回数の整列")
    def c_sym_break(self):
        """同種の看護師 n_1 < n_2 < … の夜勤回数を非増加に並べる: c[n_i] ≥ c[n_{i+1}]。
        夜勤できない集団（パート等）は対象外。eps.symmetry_break == "" なら制約なし。
        "exact" のみ健全、"relaxed" は不完全（symmetry_classes 参照。実験用）"""
        if not self.eps.symmetry_break:
            return []
        out = []
        for g in symmetry_classes(self.I, self.eps.symmetry_break):
            g = [n for n in g if night_capable(self.I, n) and n not in self.I.night_only]
            cs = [self.night_count(n) for n in g]
            out += [Labeled(cs[i] >= cs[i + 1],
                            f"{nurse_name(g[i])}の夜勤回数 ≥ {nurse_name(g[i + 1])}の夜勤回数（同種の看護師の整列）")
                    for i in range(len(g) - 1)]
        return out

    # ---- 個別要望（ε制約） ----
    @constraint("req_off", "EPS", scope="optional", title="休み要望")
    def c_req_off(self):
        """休み要望: Σ_{(n,d)∈Q^off} (1 − x[n,H+d,休]) ≤ ⌊ρ·|Q^off|⌋（ρ = eps.req_viol_ratio）"""
        cells = [(n, d) for n, ds in self.I.req_off.items() for d in ds]
        if not cells:
            return []
        cap = math.floor(self.eps.req_viol_ratio * len(cells))
        return [Labeled(cp.sum([1 - self.x(n, self.t(d), self.OFF) for n, d in cells]) <= cap,
                        f"休み要望{len(cells)}件のうち、叶わないものは{cap}件以内")]

    @constraint("req_shift", "EPS", scope="optional", title="シフト要望")
    def c_req_shift(self):
        """シフト要望: Σ_{(n,d,s)∈Q^shift} (1 − x[n,H+d,s]) ≤ ⌊ρ·|Q^shift|⌋"""
        cells = [(n, d, s) for n, ds in self.I.req_shift.items() for d, s in ds]
        if not cells:
            return []
        cap = math.floor(self.eps.req_viol_ratio * len(cells))
        return [Labeled(cp.sum([1 - self.x(n, self.t(d), s) for n, d, s in cells]) <= cap,
                        f"シフト要望{len(cells)}件のうち、叶わないものは{cap}件以内")]

    @constraint("soft_off", "EPS", scope="optional", title="希望休")
    def c_soft_off(self):
        """希望休: Σ_n Σ_{d∈R_n} (1 − x[n,H+d,休]) ≤ κ（κ = eps.soft_off_viol）"""
        viol = [1 - self.x(n, self.t(d), self.OFF) for n, ds in self.I.soft_off.items() for d in ds]
        return [Labeled(cp.sum(viol) <= self.eps.soft_off_viol,
                        f"希望休{len(viol)}件のうち、叶わないものは{self.eps.soft_off_viol}件以内")] if viol else []
