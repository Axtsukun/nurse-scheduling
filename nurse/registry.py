"""
制約の登録の仕組み。`@constraint` で NurseModel のメソッドを REGISTRY に登録し、
適用範囲（scope）に従って、どの制約を有効にするかを決める。

  layer  … 式の作り方の分類（L1構造 / L2系列 / L3個人 / L4横断 / L5規制 / EPS / SYM）
  scope  … 適用範囲。core（カーネル）/ law（層1）は常に有効、domain（層2）/ optional（層3）は外せる
  source … 根拠ID（docs/legal/constraints.yaml の id）
  title  … 制約群の日本語名（診断の説明文に使う）

制約式の名前づけ: CPMpy は変数にしか名前を付けられず、制約式には付けられない。そこで制約メソッドは、
式を `Labeled`（式 + 日本語のラベル）や `Aux`（補助変数の定義式）で包んで返してよい。ラベルのない式は
「<title> #番号」と名付けられる。build_named が `Named` に正規化し、診断（MUS）が式の id からラベルを引き戻す。
"""
from __future__ import annotations
from dataclasses import dataclass
from .instance import Instance

SCOPES = ("core", "law", "domain", "optional")
MANDATORY = ("core", "law")              # オフにできない適用範囲
TOGGLEABLE = ("domain", "optional")      # Rules.off / solve(enabled=...) で外せる適用範囲


@dataclass(frozen=True)
class ConstraintSpec:
    name: str
    layer: str                       # 式の作り方の分類
    relaxable: bool                  # 診断で指示変数を付けてよいか（緩和候補か）
    method: str                      # NurseModel のメソッド名
    scope: str = "optional"          # 適用範囲（SCOPES）
    source: tuple[str, ...] = ()     # 根拠ID
    title: str = ""                  # 制約群の日本語名


@dataclass(frozen=True)
class Labeled:
    """名前つきの制約式: 式 + その式が表す個別の規則の日本語ラベル（制約メソッドが返す）"""
    expr: object
    label: str


@dataclass(frozen=True)
class Aux:
    """補助変数の定義式（制約メソッドが返す）。補助変数は決められた値に定まるので、常に成り立つ側（hard）として
    扱い、矛盾の説明（MUS）の候補にしない。"""
    expr: object


@dataclass(frozen=True)
class Named:
    """正規化した制約式。診断で式（CPMpy の式）から制約群・ラベルを引き戻すために持つ"""
    group: str          # 制約群名（REGISTRY のキー）
    label: str          # 日本語の説明（Aux は空）
    expr: object        # CPMpy の式
    hard: bool          # True なら矛盾の説明の候補にしない（relaxable=False の群、または Aux）


REGISTRY: dict[str, ConstraintSpec] = {}


def constraint(name: str, layer: str, relaxable: bool = True, scope: str = "optional",
               source: tuple[str, ...] = (), title: str = ""):
    """制約メソッドを登録するデコレータ。メソッドは `list[Expression | Labeled | Aux]` を返すだけ（副作用なし）。
    補助変数は、そのメソッド内で生成する。"""
    if scope not in SCOPES:
        raise ValueError(f"scope は {SCOPES} のいずれか: {scope!r}")
    if name in REGISTRY:
        raise ValueError(f"制約名が重複: {name!r}")

    def deco(fn):
        REGISTRY[name] = ConstraintSpec(name, layer, relaxable, fn.__name__, scope, tuple(source), title)
        return fn
    return deco


def selected_constraints(I: Instance, enabled: set[str] | None = None) -> list[str]:
    """有効にする制約名。カーネル・層1は常に有効（enabled や Rules.off で外そうとすると ValueError）。
    層2・層3は Rules.off に含まれず、かつ enabled（None=全て）に含まれるもの。"""
    bad = [nm for nm in I.rules.off if nm not in REGISTRY or REGISTRY[nm].scope in MANDATORY]
    if bad:
        raise ValueError(f"Rules.off に指定できない制約: {bad}")
    if enabled is not None:
        missing = [nm for nm, sp in REGISTRY.items() if sp.scope in MANDATORY and nm not in enabled]
        if missing:
            raise ValueError(f"オフにできない制約を外そうとした: {missing}")
    return [nm for nm, sp in REGISTRY.items()
            if sp.scope in MANDATORY or (nm not in I.rules.off and (enabled is None or nm in enabled))]


def toggleable(names=None) -> list[str]:
    """層2・層3の制約名（実験で1つずつ外す対象）。names を渡すとその中から選ぶ"""
    return [nm for nm, sp in REGISTRY.items() if sp.scope in TOGGLEABLE and (names is None or nm in names)]
