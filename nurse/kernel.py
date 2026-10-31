"""
カーネル: 決定変数 x[n,t,s] と、制約メソッドが共通に使う式の部品。常に有効な構造制約もここに置く。
層1〜3の制約は、このクラスを継承したミックスイン（law.py / domain.py / optional.py）に定義する。
"""
from __future__ import annotations
import cpmpy as cp
from .instance import Instance, Eps, OFF_NAME
from .naming import nurse_name, day_name
from .registry import REGISTRY, ConstraintSpec, Labeled, Aux, Named, constraint, selected_constraints


class ModelKernel:
    """決定変数 x[n,t,s]（s ∈ D_n のみ生成する疎な形）を保持する。
    D_n = S_n ∪ {固定割当・履歴に現れる値}
    識別子: self.xv[n] … 看護師 n の boolvar 配列 shape=(H+T, |D_n|)
            self.col[n][s] … 値 s の列番号、 self.dom[n] … D_n（昇順リスト）
            self.active … build() で有効になった制約名（他の制約の前提を確かめるために使う）"""

    def __init__(self, I: Instance, eps: Eps):
        self.I, self.eps = I, eps
        self.H, self.T = I.n_history, I.n_days
        self.N = range(I.n_nurses)
        self.OFF = I.idx(OFF_NAME)
        fixed_values: dict[int, set[int]] = {}
        for (n, _), s in I.fixed.items():
            fixed_values.setdefault(n, set()).add(s)
        self.dom, self.col, self.xv = [], [], []
        for n in self.N:
            Dn = sorted(set(I.domain[n]) | {int(v) for v in I.history[n]} | fixed_values.get(n, set()))
            self.dom.append(Dn)
            self.col.append({s: j for j, s in enumerate(Dn)})
            self.xv.append(cp.boolvar(shape=(self.H + self.T, len(Dn)), name=f"x{n}"))
        self._work: dict[tuple[int, int], object] = {}
        self.active: frozenset[str] = frozenset(selected_constraints(I))

    # ---- 式の部品（変数は作らない） ----
    def t(self, d: int) -> int:
        """計画日 d → 時点 t = H + d"""
        return self.H + d

    def x(self, n: int, t: int, s: int):
        """x[n,t,s]。s ∉ D_n なら None（その値は取れない）"""
        j = self.col[n].get(s)
        return None if j is None else self.xv[n][t, j]

    def xs(self, n: int, t: int, ss) -> list:
        """{x[n,t,s] : s ∈ ss ∩ D_n}"""
        return [v for s in ss if (v := self.x(n, t, s)) is not None]

    def work(self, n: int, t: int):
        """w[n,t] = Σ_{s: work_s} x[n,t,s]（ExactlyOne により 0/1。変数ではなく式。同じ (n,t) は使い回す）"""
        key = (n, t)
        if key not in self._work:
            self._work[key] = cp.sum(self.xs(n, t, [s for s in self.dom[n] if self.I.shifts[s].is_work]))
        return self._work[key]

    def wvec(self, n: int):
        """w[n,·] を時点順に並べた CPMpy 式配列（長さ H+T。スライスして cp.sum に渡す）"""
        return cp.cpm_array([self.work(n, t) for t in range(self.H + self.T)])

    def cat(self, n: int, t: int, ks) -> list:
        """{x[n,t,s] : k(s) ∈ ks}"""
        return self.xs(n, t, [s for s in self.dom[n] if self.I.shifts[s].cat in ks])

    def night_count(self, n: int):
        """c[n] = Σ_d Σ_{k(s)∈𝒦^night} x[n,H+d,s]（夜勤回数の式）"""
        return cp.sum([v for d in range(self.T) for v in self.cat(n, self.t(d), self.I.night_cats)])

    def is_active(self, name: str) -> bool:
        """制約 name が今回のモデルで有効か（他の制約を前提とする式の導出に使う）"""
        return name in self.active

    def build_named(self, enabled: set[str] | None = None) -> dict[str, list[Named]]:
        """有効な制約を {制約名: [Named…]} で返す。有効な集合は selected_constraints が決める。
        制約メソッドが返す Expression / Labeled / Aux を Named（制約群・日本語ラベル・hard か）に正規化する。"""
        names = selected_constraints(self.I, enabled)
        self.active = frozenset(names)
        return {nm: [self._named(REGISTRY[nm], i, c)
                     for i, c in enumerate(getattr(self, REGISTRY[nm].method)())] for nm in names}

    @staticmethod
    def _named(spec: ConstraintSpec, i: int, c) -> Named:
        """制約メソッドの戻り値の1要素を Named にする。ラベルのない式は「<title> #番号」と名付ける。
        hard = relaxable=False の群、または Aux（補助変数の定義）"""
        if isinstance(c, Aux):
            return Named(spec.name, "", c.expr, True)
        if isinstance(c, Labeled):
            return Named(spec.name, c.label, c.expr, not spec.relaxable)
        return Named(spec.name, f"{spec.title or spec.name} #{i + 1}", c, not spec.relaxable)

    def build(self, enabled: set[str] | None = None) -> dict[str, list]:
        """有効な制約を {制約名: 式のリスト} で返す（求解用。名前・ラベルは付けない）"""
        return {nm: [x.expr for x in items] for nm, items in self.build_named(enabled).items()}

    # ---- カーネルの構造制約（常に有効） ----
    @constraint("struct", "L1", relaxable=False, scope="core", title="各時点でちょうど1つの勤務")
    def c_struct(self):
        """Σ_{s∈D_n} x[n,t,s] = 1（ExactlyOne）"""
        return [c for n in self.N for c in (self.xv[n].sum(axis=1) == 1)]

    @constraint("history", "L1", relaxable=False, scope="core", title="前月末の実績")
    def c_history(self):
        """x[n,t,h_{n,t}] = 1（t < H, 前月末の実績）"""
        return [self.x(n, t, int(self.I.history[n, t])) == 1
                for n in self.N for t in range(self.H)]

    @constraint("fixed", "L3", scope="core", source=("LAW-PAID-LEAVE",), title="固定割当（確定休・有休など）")
    def c_fixed(self):
        """固定割当 x[n,H+d,s] = 1。fixed_only の値は固定日以外で x = 0（この側は定義的なので Aux）"""
        I = self.I
        out = [Labeled(self.x(n, self.t(d), s) == 1,
                       f"{nurse_name(n)} {day_name(I, d)}: {I.shifts[s].name}に固定（確定休・有休など）")
               for (n, d), s in sorted(I.fixed.items())]               # 看護師・日付順（説明が読みやすい）
        for n in self.N:
            for s in self.dom[n]:
                if I.shifts[s].fixed_only:
                    out += [Aux(self.x(n, self.t(d), s) == 0) for d in range(self.T)
                            if I.fixed.get((n, d)) != s]
        return out
