"""
制約の登録の仕組み。`@constraint` で NurseModel のメソッドを REGISTRY に登録し、
適用範囲（scope）に従って、どの制約を有効にするかを決める。

  layer  … 式の作り方の分類（L1構造 / L2系列 / L3個人 / L4横断 / L5規制 / EPS / SYM）
  scope  … 適用範囲。core（カーネル）/ law（層1）は常に有効、domain（層2）/ optional（層3）は外せる
  source … 根拠ID（docs/legal/constraints.yaml の id）
  title  … 制約クラス名（日本語・短い。診断の表示 `[クラス名]` に使う。docs/design/constraint-message-spec.md）

制約の名前: CPMpy は変数にしか名前を付けられず、制約式には付けられない。そこで制約メソッドは、式を
`Item`（式 + 対象 + パラメータ）や `Aux`（補助変数の定義式）で包んで返してよい。build_named が `Named` に
正規化し、全制約に ID（`{群}-{群内連番4桁}`）を付ける。メッセージは `Named.message` が、対象とパラメータから
整形器（message.Renderer）で作る（診断のときだけ生成。build では文字列を作らない）。
"""
from __future__ import annotations
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import TypeVar
from cpmpy.expressions.core import Expression
from .instance import Instance
from .message import Renderer
from .codes import InputError

SCOPES = ("core", "law", "domain", "optional")
MANDATORY = ("core", "law")              # オフにできない適用範囲
TOGGLEABLE = ("domain", "optional")      # Rules.off / solve(enabled=...) で外せる適用範囲


@dataclass(frozen=True)
class ConstraintSpec:
    """登録された制約1件の定義。`@constraint` が REGISTRY に入れる。"""
    name: str                        # 制約名（registry の識別子）
    layer: str                       # 式の作り方の分類
    relaxable: bool                  # 診断で指示変数を付けてよいか（緩和候補か）
    method: str                      # NurseModel のメソッド名
    scope: str = "optional"          # 適用範囲（SCOPES）
    source: tuple[str, ...] = ()     # 根拠ID
    title: str = ""                  # 制約クラス名（日本語・短い）


@dataclass(frozen=True)
class Item:
    """個別の規則: 式 + 対象（target）+ パラメータ（params）。制約メソッドが返す。
    target は部品（message.Nurse / Day / DaySpan / Span / Cat / Shift / 文字列）の並び、
    params は message.Param の並び。メッセージは、この2つから整形器が作る。"""
    expr: object                # 制約式
    target: tuple = ()          # 対象の部品の並び
    params: tuple = ()          # Param の並び


@dataclass(frozen=True)
class Aux:
    """補助変数の定義式（制約メソッドが返す）。補助変数は決められた値に定まるので、常に成り立つ側（hard）として
    扱い、矛盾の説明（MUS）の候補にしない。"""
    expr: object                # 補助変数の定義式


Constraints = Sequence[Expression | Item | Aux]
"""制約メソッドの戻り値の型。CPMpy の式、または式を包んだ `Item`（対象・パラメータつき）・`Aux`（補助変数の定義）の並び（読み取り専用）。"""
ConstraintList = list[Expression | Item | Aux]
"""`Constraints` を組み立てる途中の、追加できるリスト。"""
_F = TypeVar("_F", bound=Callable[..., object])


@dataclass(frozen=True, eq=False)
class Named:
    """正規化した制約。診断で式（CPMpy の式）から ID・制約クラス・メッセージを引き戻すために持つ"""
    id: str             # {群}-{群内連番4桁}。全制約でモデル全体で一意
    group: str          # 制約群名（REGISTRY のキー）
    title: str          # 制約クラス名（日本語）
    expr: object        # CPMpy の式
    hard: bool          # True なら矛盾の説明の候補にしない（relaxable=False の群、または Aux）
    target: tuple = ()  # メッセージの対象（構造化データ）
    params: tuple = ()  # メッセージのパラメータ（構造化データ）
    ctx: Instance | None = None   # 整形の表示方針（開始日・表示名）の元

    @property
    def message(self) -> str:
        """メッセージ（`<対象> <名前>=<値> …`）。対象・パラメータがなければ空（背景・補助変数の定義）"""
        if self.ctx is None or not (self.target or self.params):
            return ""
        return Renderer(self.ctx).message(self.target, self.params)

    @property
    def line(self) -> str:
        """表示行 `{ID}  [{クラス名}] {メッセージ}`"""
        return f"{self.id}  [{self.title}] {self.message}"


REGISTRY: dict[str, ConstraintSpec] = {}


def constraint(name: str, layer: str, relaxable: bool = True, scope: str = "optional",
               source: tuple[str, ...] = (), title: str = "") -> Callable[[_F], _F]:
    """制約メソッドを登録するデコレータ。メソッドは `list[Expression | Item | Aux]` を返すだけ（副作用なし）。
    補助変数は、そのメソッド内で生成する。

    Args:
        name: 制約名（registry の識別子。重複不可）。
        layer: 式の作り方の分類（L1〜L5、EPS、SYM）。
        relaxable: 診断で緩和候補にしてよいか。
        scope: 適用範囲（`core` / `law` / `domain` / `optional`）。
        source: 根拠ID（`docs/legal/constraints.yaml` の id）の並び。
        title: 制約クラス名（日本語・短い）。

    Returns:
        制約メソッドを登録するデコレータ。"""
    if scope not in SCOPES:
        raise InputError(f"scope は {SCOPES} のいずれか: {scope!r}")
    if name in REGISTRY:
        raise InputError(f"制約名が重複: {name!r}")

    def deco(fn: _F) -> _F:
        REGISTRY[name] = ConstraintSpec(name, layer, relaxable, fn.__name__, scope, tuple(source), title)
        return fn
    return deco


def selected_constraints(I: Instance, enabled: set[str] | None = None) -> list[str]:
    """有効にする制約名。カーネル・層1は常に有効（enabled や Rules.off で外そうとすると ValueError）。
    層2・層3は Rules.off に含まれず、かつ enabled（None=全て）に含まれるもの。

    Args:
        I: 入力データ（`Rules.off` を使う）。
        enabled: 有効にする制約名（`None` なら全て）。

    Returns:
        有効な制約名のリスト（登録順）。"""
    bad = [nm for nm in I.rules.off if nm not in REGISTRY or REGISTRY[nm].scope in MANDATORY]
    if bad:
        raise InputError(f"Rules.off に指定できない制約: {bad}")
    if enabled is not None:
        missing = [nm for nm, sp in REGISTRY.items() if sp.scope in MANDATORY and nm not in enabled]
        if missing:
            raise InputError(f"オフにできない制約を外そうとした: {missing}")
    return [nm for nm, sp in REGISTRY.items()
            if sp.scope in MANDATORY or (nm not in I.rules.off and (enabled is None or nm in enabled))]


def toggleable(names: Collection[str] | None = None) -> list[str]:
    """層2・層3の制約名（実験で1つずつ外す対象）。names を渡すとその中から選ぶ

    Args:
        names: 絞り込む制約名（`None` なら全て）。

    Returns:
        層2・層3の制約名のリスト。"""
    return [nm for nm, sp in REGISTRY.items() if sp.scope in TOGGLEABLE and (names is None or nm in names)]
