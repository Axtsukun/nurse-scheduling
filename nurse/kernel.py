"""
カーネル: 決定変数 x[n,t,s] と、制約メソッドが共通に使う式の部品。常に有効な構造制約もここに置く。
層1〜3の制約は、このクラスを継承したミックスイン（law.py / domain.py / optional.py）に定義する。
"""
from __future__ import annotations
from collections.abc import Collection, Iterable
import logging
import re
import cpmpy as cp
from cpmpy.expressions.core import Expression
from cpmpy.expressions.variables import NDVarArray
from cpmpy.transformations.get_variables import get_variables
from .codes import InputError
from .instance import Instance, Eps, OFF_NAME
from .message import Nurse, Day, Shift, Param
from .registry import REGISTRY, ConstraintSpec, Item, Aux, Named, Constraints, ConstraintList, constraint, selected_constraints


log = logging.getLogger(__name__)


def _as_expression(c: object) -> Expression:
    """制約を、必ず CPMpy の式にする。`cp.sum([]) >= 1` のように、変数が1つもない比較は、CPMpy の式ではなく
    Python の定数（`True` / `False`）になる（例: 夜勤リーダーの資格者の中に、その区分の勤務を取れる人がいない）。
    定数のままだと、MUS の `toplevel_list` が別のオブジェクト（`BoolVal`）に作り替えて、制約との対応づけ（`id()`）が壊れ、
    矛盾の診断が落ちる。`BoolVal` に包めば、定数の `False` も、ほかの制約と同じ1本の制約として MUS に現れる。

    Args:
        c: 制約メソッドが返した式（または Python の真偽値）。

    Returns:
        CPMpy の式。式はそのまま、Python の真偽値は `BoolVal` に包んで返す。"""
    return c if isinstance(c, Expression) else cp.BoolVal(bool(c))


class ModelKernel:
    """決定変数 `x[n,t,s]`（s ∈ D_n のみ生成する疎な形）を保持する。D_n = S_n ∪ {固定割当・履歴に現れる値}。

    識別子:

    - `self.xv[n]` … 看護師 n の 0/1 の intvar 配列。shape=(H+T, |D_n|)
    - `self.col[n][s]` … 値 s の列番号
    - `self.dom[n]` … D_n（昇順リスト）
    - `self.active` … `build()` で有効になった制約名（他の制約の前提を確かめるために使う）"""

    def __init__(self, I: Instance, eps: Eps) -> None:
        """決定変数 `x` を看護師ごとに作る（取れる値の集合 D_n だけの疎な形）。

        Args:
            I: 入力データ。
            eps: ε 制約（品質要件）の閾値。"""
        self.I, self.eps = I, eps
        self.H, self.T = I.n_history, I.n_days
        self.N = range(I.n_nurses)  # 看護師の添字の範囲 𝒩
        self.OFF = I.idx(OFF_NAME)  # 「休」の勤務の添字
        fixed_values: dict[int, set[int]] = {}
        for (n, _), s in I.fixed.items():
            fixed_values.setdefault(n, set()).add(s)
        self.dom, self.col, self.xv = [], [], []
        for n in self.N:
            Dn = sorted(set(I.domain[n]) | {int(v) for v in I.history[n]} | fixed_values.get(n, set()))
            self.dom.append(Dn)
            self.col.append({s: j for j, s in enumerate(Dn)})
            self.xv.append(cp.intvar(0, 1, shape=(self.H + self.T, len(Dn)), name=f"x{n}"))
            log.debug("決定変数 x%d: 形 %s（取れる値 %d 個）", n, self.xv[n].shape, len(Dn))
        log.info("決定変数 x を宣言: 看護師 %d 人 × 時点 %d（履歴 %d + 計画 %d）、変数の総数 %d",
                 I.n_nurses, self.H + self.T, self.H, self.T, sum(v.size for v in self.xv))
        self._work: dict[tuple[int, int], object] = {}
        self._named_cache: dict[tuple[str, ...], dict[str, list[Named]]] = {}   # 有効な制約名 → build_named の結果（作り直さない）
        self.active: frozenset[str] = frozenset(selected_constraints(I))  # build() で有効になった制約名

    # ---- 式の部品（変数は作らない） ----
    def t(self, d: int) -> int:
        """計画日 d → 時点 t = H + d

        Args:
            d: 計画日（0 始まり）。

        Returns:
            時点 t = H + d（H は履歴の日数）。"""
        return self.H + d

    def x(self, n: int, t: int, s: int) -> Expression | None:
        """x[n,t,s]。s ∉ D_n なら None（その値は取れない）

        Args:
            n: 看護師の添字。
            t: 時点。
            s: 勤務の添字。

        Returns:
            決定変数 `x[n,t,s]`。s が D_n に無ければ `None`。"""
        j = self.col[n].get(s)
        return None if j is None else self.xv[n][t, j]

    def x_taken(self, n: int, t: int, s: int) -> Expression:
        """x[n,t,s]。その値を取れること（s ∈ D_n）が前提の制約で使う。取れなければ `InputError`（None を式に混ぜない）。

        Args:
            n: 看護師の添字。
            t: 時点。
            s: 勤務の添字。

        Returns:
            決定変数 `x[n,t,s]`。
        """
        v = self.x(n, t, s)
        if v is None:
            raise InputError(f"看護師 {n} は勤務 {self.I.shifts[s].name!r} を取れない（取れる値の集合に無い）が、"
                             f"時点 {t} でその値を前提とする制約（要望など）がある")
        return v

    def xs(self, n: int, t: int, ss: Iterable[int]) -> list[Expression]:
        """{x[n,t,s] : s ∈ ss ∩ D_n}

        Args:
            n: 看護師の添字。
            t: 時点。
            ss: 勤務の添字の集まり。

        Returns:
            `x[n,t,s]` のリスト（D_n に無い s は除く）。"""
        return [v for s in ss if (v := self.x(n, t, s)) is not None]

    def work(self, n: int, t: int) -> Expression:
        """w[n,t] = Σ_{s: work_s} x[n,t,s]（ExactlyOne により 0/1。変数ではなく式。同じ (n,t) は使い回す）

        Args:
            n: 看護師の添字。
            t: 時点。

        Returns:
            勤務かを表す式 `w[n,t]`（0 または 1）。"""
        key = (n, t)
        if key not in self._work:
            self._work[key] = cp.sum(self.xs(n, t, [s for s in self.dom[n] if self.I.shifts[s].is_work]))
        return self._work[key]

    def wvec(self, n: int) -> NDVarArray:
        """w[n,·] を時点順に並べた CPMpy 式配列（長さ H+T。スライスして cp.sum に渡す）

        Args:
            n: 看護師の添字。

        Returns:
            `w[n,·]` を時点順に並べた CPMpy の式配列（長さ H+T）。"""
        return cp.cpm_array([self.work(n, t) for t in range(self.H + self.T)])

    def cat(self, n: int, t: int, ks: Collection[str]) -> list[Expression]:
        """{x[n,t,s] : k(s) ∈ ks}

        Args:
            n: 看護師の添字。
            t: 時点。
            ks: 勤務区分の集まり（`ShiftDef.cat`）。

        Returns:
            区分が `ks` に入る勤務の `x[n,t,s]` のリスト。"""
        return self.xs(n, t, [s for s in self.dom[n] if self.I.shifts[s].cat in ks])

    def night_count(self, n: int) -> Expression:
        """c[n] = Σ_d Σ_{k(s)∈𝒦^night} x[n,H+d,s]（夜勤回数の式）

        Args:
            n: 看護師の添字。

        Returns:
            夜勤回数 `c[n]` の式。"""
        return cp.sum([v for d in range(self.T) for v in self.cat(n, self.t(d), self.I.night_cats)])

    def is_active(self, name: str) -> bool:
        """制約 name が今回のモデルで有効か（他の制約を前提とする式の導出に使う）

        Args:
            name: 制約名。

        Returns:
            有効なら `True`。"""
        return name in self.active

    def build_named(self, enabled: set[str] | None = None) -> dict[str, list[Named]]:
        """有効な制約を {制約名: [Named…]} で返す。有効な集合は selected_constraints が決める。
        制約メソッドが返す Expression / Item / Aux を Named（ID・制約クラス・対象・パラメータ・hard か）に正規化する。
        ID は `{群}-{群内連番4桁}`（連番は、その群の制約メソッドが返した順。補助変数の定義も数える）。

        **同じ有効な制約の集合なら、最初に作った結果を再利用する**（作り直さない）。求解（`build`）と診断（MUS）が、同じ式・
        同じ補助変数を共有するため、診断は `Named` を取り直さない。モデルの入力（`I`・`eps`）は生成後に変えない前提。
        返す辞書とリストは呼び出しごとの新しい入れ物で、中の `Named`（と式）は共有する。

        Args:
            enabled: 有効にする制約名（`None` なら全て）。カーネル・層1は外せない。

        Returns:
            `{制約名: [Named…]}`。"""
        names = selected_constraints(self.I, enabled)
        self.active = frozenset(names)
        key = tuple(names)
        built = self._named_cache.get(key)
        if built is None:
            built = {nm: [self._named(REGISTRY[nm], i, c)
                          for i, c in enumerate(getattr(self, REGISTRY[nm].method)())] for nm in names}
            self._named_cache[key] = built
            self._log_definitions(built)
        else:
            log.debug("定義済みの制約を再利用（作り直さない）: %d 本（%d 群）", sum(map(len, built.values())), len(built))
        return {nm: list(items) for nm, items in built.items()}

    def _log_definitions(self, built: dict[str, list[Named]]) -> None:
        """定義した制約（個別内容と総数）と、制約の中で宣言された補助変数（個別内容と総数）をログに残す。
        個別内容は DEBUG、総数は INFO。ログが無効なら、数える処理もしない。

        Args:
            built: `build_named` が作った `{制約名: [Named…]}`。"""
        if not log.isEnabledFor(logging.INFO):
            return
        for nm, items in built.items():
            log.info("制約を定義: %s [%s] %d 本（うち hard %d）", nm, REGISTRY[nm].title, len(items), sum(x.hard for x in items), stacklevel=2)
        log.info("制約の総数: %d 本（%d 群）", sum(map(len, built.values())), len(built), stacklevel=2)
        x_ids = {id(v) for xv in self.xv for v in xv.flat}
        aux = {id(v): v for items in built.values() for x in items for v in get_variables(x.expr) if id(v) not in x_ids}
        kinds: dict[str, int] = {}
        for v in aux.values():
            found = re.match(r"[A-Za-z]+", v.name)
            kind = found.group(0) if found else "?"
            kinds[kind] = kinds.get(kind, 0) + 1
        log.info("補助変数の総数: %d（種類別 %s）", len(aux), ", ".join(f"{k}={n}" for k, n in sorted(kinds.items())) or "なし", stacklevel=2)
        if log.isEnabledFor(logging.DEBUG):
            for items in built.values():
                for x in items:
                    log.debug("制約: %s%s", x.line, "（hard）" if x.hard else "", stacklevel=2)
            for v in sorted(aux.values(), key=lambda v: v.name):
                log.debug("補助変数: %s", v.name, stacklevel=2)

    def _named(self, spec: ConstraintSpec, i: int, c: Expression | Item | Aux) -> Named:
        """制約メソッドの戻り値の1要素を Named にする（i は群内の0始まりの位置）。
        hard = relaxable=False の群、または Aux（補助変数の定義）。メッセージはここでは作らない（Named.message が遅延生成）

        Args:
            spec: 制約の登録情報。
            i: 群内の0始まりの位置。
            c: 制約メソッドが返した要素（式・`Item`・`Aux`）。

        Returns:
            正規化した `Named`（ID・制約クラス・式・hard か・対象・パラメータ）。"""
        ident = f"{spec.name}-{i + 1:04d}"
        if isinstance(c, Aux):
            return Named(ident, spec.name, spec.title, _as_expression(c.expr), True, ctx=self.I)
        if isinstance(c, Item):
            return Named(ident, spec.name, spec.title, _as_expression(c.expr), not spec.relaxable, c.target, c.params, self.I)
        return Named(ident, spec.name, spec.title, _as_expression(c), not spec.relaxable, ctx=self.I)

    def build(self, enabled: set[str] | None = None) -> dict[str, list]:
        """有効な制約を {制約名: 式のリスト} で返す（求解用）

        Args:
            enabled: 有効にする制約名（`None` なら全て）。

        Returns:
            `{制約名: 式のリスト}`（求解用）。"""
        return {nm: [x.expr for x in items] for nm, items in self.build_named(enabled).items()}

    # ---- カーネルの構造制約（常に有効） ----
    @constraint("struct", "L1", relaxable=False, scope="core", title="勤務一意")
    def c_struct(self) -> Constraints:
        """Σ_{s∈D_n} x[n,t,s] = 1（ExactlyOne）

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        return [c for n in self.N for c in (self.xv[n].sum(axis=1) == 1)]

    @constraint("history", "L1", relaxable=False, scope="core", title="履歴固定")
    def c_history(self) -> Constraints:
        """x[n,t,h_{n,t}] = 1（t < H, 前月末の実績）

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        return [self.x(n, t, int(self.I.history[n, t])) == 1
                for n in self.N for t in range(self.H)]

    @constraint("fixed", "L3", scope="core", source=("LAW-PAID-LEAVE",), title="固定割当")
    def c_fixed(self) -> Constraints:
        """固定割当 x[n,H+d,s] = 1。fixed_only の値は固定日以外で x = 0（この側は定義的なので Aux）

        Returns:
            制約の並び（CPMpy の式、または式を包んだ `Item`・`Aux`。`Constraints`）。"""
        I = self.I
        out: ConstraintList = [Item(self.x(n, self.t(d), s) == 1, (Nurse(n), Day(d)), (Param("勤務", Shift(s)),))
               for (n, d), s in sorted(I.fixed.items())]               # 看護師・日付順（説明が読みやすい）
        for n in self.N:
            for s in self.dom[n]:
                if I.shifts[s].fixed_only:
                    out += [Aux(self.x(n, self.t(d), s) == 0) for d in range(self.T)
                            if I.fixed.get((n, d)) != s]
        return out
