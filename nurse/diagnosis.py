"""
解けないときの診断。原因は2つに分けて扱う:
  - 時間切れ … 解も、解が無い証明も得られなかった（制約矛盾かどうかは不明）
  - 制約矛盾 … 解が無いと証明できた。同時には成り立たない最小の制約の集合（MUS）を日本語で説明する

MUS（極小充足不能部分集合）の求め方
  - cpmpy.tools.mus を、PB ソルバー exact で使う（決定事項。実測で CP-SAT より速い。README 4.5 節）。
    求解は CP-SAT（solver.SOLVE_SOLVER）、MUS は exact（MUS_SOLVER）と決めており、切り替えの自動化はしない。
  - 制約を hard と soft に分けて渡す（hard は仮定を持たないので、soft だけが絞り込みの対象になり速い）。
      背景 … 構造・履歴・補助変数の定義（relaxable=False の群と Aux）。常に成り立たせる
      前提 … カーネル・層1の制約（固定割当・労基法の休日）。scope が MANDATORY = オフにできない
      soft … 層2・層3の制約（scope が TOGGLEABLE = 外せる）。矛盾の説明の主役
  - 一段階で求める（制約群を単位にした先行の絞り込みは、実測で一段階より遅かったので採用しない）。
  - CPMpy は制約式に名前を付けられないので、式の id から Named（制約群・日本語ラベル）を引き戻す。
"""
from __future__ import annotations
from dataclasses import dataclass, field
import time
import cpmpy as cp
from cpmpy.tools import mus
from .instance import Instance, Eps
from .registry import REGISTRY, MANDATORY, TOGGLEABLE, Named
from .solver import solve, SOLVE_SOLVER

MUS_SOLVER = "exact"                       # 決定事項: MUS の計算は exact。変更する場合はユーザーに確認する
FEASIBLE, TIMEOUT, CONFLICT = "feasible", "timeout", "conflict"
SCOPE_NAMES = {"core": "カーネル", "law": "層1 労働法規", "domain": "層2 病院・看護職共通", "optional": "層3 任意"}


@dataclass
class Diagnosis:
    """診断の結果。status は FEASIBLE / TIMEOUT / CONFLICT のいずれか"""
    status: str
    detail: str = ""                                          # ソルバーの状態など
    items: list[Named] = field(default_factory=list)          # CONFLICT: MUS を構成する個別の制約（定義順）
    hard_only: bool = False                                   # CONFLICT: True ならオフにできない制約（前提）同士の矛盾
    times: dict[str, float] = field(default_factory=dict)     # solve / mus_soft / mus_given の秒数
    mus_solver: str = ""                                      # MUS に使ったソルバー

    @property
    def groups(self) -> list[str]:
        """MUS に含まれる制約群名（登録順）"""
        names = {x.group for x in self.items}
        return [g for g in REGISTRY if g in names]

    @property
    def given_items(self) -> list[Named]:
        """MUS のうち、オフにできない制約（カーネル・層1。前提として固定したもの）"""
        return [x for x in self.items if REGISTRY[x.group].scope in MANDATORY]

    @property
    def soft_items(self) -> list[Named]:
        """MUS のうち、外せる制約（層2・層3）"""
        return [x for x in self.items if REGISTRY[x.group].scope in TOGGLEABLE]

    def explain(self) -> str:
        """診断結果の日本語の説明"""
        return explain(self)


def _require_solver(name: str) -> str:
    """MUS に使うソルバーが導入済みか確かめる。未導入なら（黙って別のソルバーに替えず）RuntimeError"""
    if name not in cp.SolverLookup.supported():
        raise RuntimeError(f"MUS ソルバー {name!r} が使えない（導入済み: {cp.SolverLookup.supported()}）。"
                           "exact は `pip install exact` で入れる")
    return name


def _check(exprs: list, time_limit: float) -> str:
    """exprs が充足可能かを求解用のソルバー（CP-SAT）で調べる。"sat" / "unsat" / "unknown"（時間切れ）"""
    s = cp.SolverLookup.get(SOLVE_SOLVER, cp.Model(exprs))
    if s.solve(time_limit=time_limit):
        return "sat"
    return "unsat" if s.status().exitstatus.name == "UNSATISFIABLE" else "unknown"


def diagnose(I: Instance, eps: Eps, time_limit: float = 30, mus_solver: str = MUS_SOLVER) -> Diagnosis:
    """解けるか調べ、解けなければ原因（時間切れ / 制約矛盾）を返す。
    1. 通常のモデルを求解用のソルバー（CP-SAT）で解く。解ければ FEASIBLE、時間切れなら TIMEOUT（矛盾かどうかは不明）。
    2. 実行不能と確定したときだけ MUS を求める（指示変数つきのモデルは桁違いに遅いので、解ける問題では解かない）。
       a. 背景と前提（カーネル・層1）だけで矛盾するなら、前提の中から MUS（hard_only=True。入力データと法令の矛盾）。
       b. そうでなければ、前提を hard として固定し、soft（層2・層3）の MUS を求め、
          その矛盾に関わる前提（例: 固定割当）を追加で絞り込む。"""
    first = solve(I, eps, time_limit=time_limit)
    times = {"solve": first.times["solve"]}
    state = first.status.exitstatus.name
    if first.ok:
        return Diagnosis(FEASIBLE, state, times=times)
    if state != "UNSATISFIABLE":
        return Diagnosis(TIMEOUT, f"{time_limit:g}秒で解も矛盾の証明も得られなかった（{state}）", times=times)

    flat = [x for items in first.model.build_named().values() for x in items]
    background = [x.expr for x in flat if x.hard]
    given = [x for x in flat if not x.hard and REGISTRY[x.group].scope in MANDATORY]
    soft = [x for x in flat if not x.hard and REGISTRY[x.group].scope in TOGGLEABLE]
    solver = _require_solver(mus_solver)
    position = {id(x.expr): i for i, x in enumerate(flat)}              # 定義順（日付順など）で並べる

    def pick(core, pool: list[Named]) -> list[Named]:
        by_id = {id(x.expr): x for x in pool}
        return sorted((by_id[id(c)] for c in core if id(c) in by_id), key=lambda x: position[id(x.expr)])

    t0 = time.perf_counter()
    given_exprs = [x.expr for x in given]
    verdict = _check(background + given_exprs, time_limit)
    if verdict == "unknown":
        return Diagnosis(TIMEOUT, f"オフにできない制約だけの充足判定が{time_limit:g}秒で終わらなかった", times=times)
    if verdict == "unsat":                                              # a. 前提同士の矛盾（soft を外す余地がない）
        if not given:
            raise RuntimeError("背景の制約だけで矛盾している（モデルの不具合）")
        items = pick(mus(given_exprs, background, solver=solver), given)
        times["mus_given"] = time.perf_counter() - t0
        return Diagnosis(CONFLICT, state, items, True, times, solver)

    core = mus([x.expr for x in soft], background + given_exprs, solver=solver)          # b. soft の MUS（一段階）
    soft_items = pick(core, soft)
    times["mus_soft"] = time.perf_counter() - t0
    t1 = time.perf_counter()
    involved = []                                                       # soft の矛盾に関わる前提だけに絞る
    if given:
        involved = pick(mus(given_exprs, background + [x.expr for x in soft_items], solver=solver), given)
    times["mus_given"] = time.perf_counter() - t1
    items = sorted(involved + soft_items, key=lambda x: position[id(x.expr)])
    return Diagnosis(CONFLICT, state, items, False, times, solver)


def _summarize(items: list[Named]) -> list[tuple[Named, int]]:
    """同じラベルの制約を数えてまとめる（順序は保つ）"""
    out: dict[tuple[str, str], list] = {}
    for x in items:
        out.setdefault((x.group, x.label), [x, 0])[1] += 1
    return [(x, n) for x, n in out.values()]


def explain(d: Diagnosis) -> str:
    """Diagnosis の日本語の説明文"""
    if d.status == FEASIBLE:
        return "【診断】解が見つかりました（制約の矛盾はありません）。"
    if d.status == TIMEOUT:
        return ("【診断】時間切れ: 解も、解が無い証明も得られませんでした（制約矛盾かどうかは不明です）。\n"
                f"  状態: {d.detail}\n"
                "  対処: time_limit を延ばす、規模を小さくする、night_band など求解を重くする条件を見直す、"
                "precheck(I, eps) で需要と供給の単純な不足がないか確かめる。")
    lines = ["【診断】制約矛盾: 解が存在しません。次の制約は同時には成り立ちません。",
             "  （最小不整合部分集合 MUS: このうち1つでも外せば、残りは矛盾しなくなります）", ""]
    lines.append(f"■ 関係する制約群（{len(d.groups)}群）")
    for g in d.groups:
        sp = REGISTRY[g]
        src = f" 根拠: {', '.join(sp.source)}" if sp.source else ""
        lines.append(f"  - {g}: {sp.title or g}（{SCOPE_NAMES[sp.scope]}）{src}")

    def block(title: str, items: list[Named]) -> None:
        lines.extend(["", f"■ {title}（{len(items)}件）"])
        for x, n in _summarize(items):
            lines.append(f"  [{x.group}] {x.label}" + (f"  ×{n}" if n > 1 else ""))

    if d.hard_only:
        block("矛盾する制約: オフにできない制約同士（入力データと法令）", d.items)
    else:
        if d.given_items:
            block("前提としている制約（オフにできない。カーネル・層1）", d.given_items)
        block("矛盾の原因になっている制約（外せる。層2・層3）", d.soft_items)

    lines += ["", "■ 対処"]
    if d.hard_only:
        lines.append("  - これらはオフにできません。入力データ（固定割当・需要・公休数など）を見直してください。")
    else:
        lines.append(f"  - 外せる制約（{', '.join(g for g in d.groups if REGISTRY[g].scope in TOGGLEABLE)}）は、"
                     "パラメータ（需要・上限など）の調整か、Rules.off での除外で緩められます。")
        if d.given_items:
            lines.append(f"  - 前提の制約（{', '.join(g for g in d.groups if REGISTRY[g].scope in MANDATORY)}）はオフにできません。"
                         "入力データ（固定割当など）を見直す手もあります。")
    t = d.times
    lines += ["", f"（計算: 通常の求解 {t.get('solve', 0):.1f}秒 / soft のMUS {t.get('mus_soft', 0):.1f}秒 / "
                  f"前提のMUS {t.get('mus_given', 0):.1f}秒、MUSソルバー {d.mus_solver}）"]
    return "\n".join(lines)
