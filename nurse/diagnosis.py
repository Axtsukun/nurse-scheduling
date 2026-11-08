"""解けないときの診断。原因は2つに分けて扱う:

- **時間切れ** … 解も、解が無い証明も得られなかった（制約矛盾かどうかは不明）
- **制約矛盾** … 解が無いと証明できた。同時には成り立たない最小の制約の集合（MUS）を日本語で説明する

## MUS（極小充足不能部分集合）の求め方

- `cpmpy.tools.mus` を、PB ソルバー exact で使う（決定事項。実測で CP-SAT より速い。README 4.5 節）。
  求解は CP-SAT（`solver.SOLVE_SOLVER`）、MUS は exact（`MUS_SOLVER`）と決めており、切り替えの自動化はしない。
- 制約を hard と soft に分けて渡す（hard は仮定を持たないので、soft だけが絞り込みの対象になり速い）:
    - **背景** … 構造・履歴・補助変数の定義（`relaxable=False` の群と Aux）。常に成り立たせる
    - **前提** … カーネル・層1の制約（固定割当・労基法の休日）。scope が MANDATORY = オフにできない
    - **soft** … 層2・層3の制約（scope が TOGGLEABLE = 外せる）。矛盾の説明の主役
- 一段階で求める（制約群を単位にした先行の絞り込みは、実測で一段階より遅かったので採用しない）。
- **MUS は別プロセスで求める**（`nurse.mus_worker`）。exact を含む MUS のソルバーは、そのプロセスの中だけで動き、
  診断を呼ぶプロセスは exact を読み込まない。式は pickle で渡し、結果は soft の添字で受け取る（`nurse.mus_protocol`）。
  `fork` を使わないので、Windows や macOS でも動く。
- 時間制限（`mus_time_limit`）: `cpmpy.tools.mus` 自体には時間制限がなく、極端な矛盾では戻らない。MUS プロセスの中で:
    1. **事前候補**: MUS の前に、仮定つきの求解 1 回で矛盾の集合（コア。最小とは限らない）を取り、すぐに書き出す。
    2. `mus` で最小にする。`mus_time_limit` 秒で終わらなければ、診断がプロセスを止める。終われば最小（`minimal=True`）。
    3. 打ち切ったら、1. の事前候補を結果にする（`minimal=False`。矛盾する集合だが、不要な制約を含む可能性がある）。

    事前候補（1.）が時間内に終わらなければ、矛盾は確定しているが原因の制約は特定できない（`items` が空、`minimal=False`）。
    時間は、プロセスの起動と式の復元が終わってから数える（その所要は `Diagnosis.times["mus_startup"]`）。

- MUS プロセスは soft の添字を返すので、診断は添字で Named（制約群・日本語ラベル）を引き戻す。"""
from __future__ import annotations
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import cpmpy as cp
from cpmpy.expressions.core import Expression
from .codes import ReturnCode, SolverUnavailableError, InternalError
from .config import load_settings
from .instance import Instance, Eps
from .logger import attach_engine_log
from .mus_protocol import (CORE_FILE, CORE_KEYS, MUS, READY_FILE, REQUEST_FILE, RESPONSE_FILE, RESPONSE_KEYS, SAT, UNAVAILABLE, UNKNOWN,
                           ProtocolError, make_request, read_message, write_message)
from .registry import REGISTRY, MANDATORY, TOGGLEABLE, Named
from .solver import solve, SOLVE_SOLVER

MUS_SOLVER = "exact"                       # 決定事項: MUS の計算は exact。変更する場合はユーザーに確認する
STARTUP_LIMIT = 120.0                      # MUS プロセスの起動と式の復元が終わるまでの上限（秒）。超えたら止める
CORE_GRACE = 5.0                           # 事前候補の求解に、時間制限（mus_time_limit）へ足す猶予（秒）。ソルバーが時間制限を守りきれないことがある
_POLL = 0.02                               # MUS プロセスの状態を見る間隔（秒）
FEASIBLE, TIMEOUT, CONFLICT = "feasible", "timeout", "conflict"
SCOPE_NAMES = {"core": "カーネル", "law": "層1 労働法規", "domain": "層2 病院・看護職共通", "optional": "層3 任意"}


log = logging.getLogger(__name__)


@dataclass
class Diagnosis:
    """診断の結果。status は FEASIBLE / TIMEOUT / CONFLICT のいずれか"""
    status: str      # FEASIBLE / TIMEOUT / CONFLICT
    detail: str = ""                                          # ソルバーの状態など
    items: list[Named] = field(default_factory=list)          # CONFLICT: MUS を構成する個別の制約（定義順）
    hard_only: bool = False                                   # CONFLICT: True ならオフにできない制約（前提）同士の矛盾
    times: dict[str, float] = field(default_factory=dict)     # solve / mus_soft / mus_given の秒数。mus_startup = MUS プロセスの起動・式の復元の合計（mus_* に含まれる）
    mus_solver: str = ""                                      # MUS に使ったソルバー
    minimal: bool = True                                      # CONFLICT: False なら時間制限で打ち切り（最小でない可能性。items が空なら特定できず）

    @property
    def code(self) -> ReturnCode:
        """リターンコード（nurse/codes.py）: 解あり OK / 求解の時間切れ / 制約矛盾（最小・MUS 時間切れ・原因不明）"""
        if self.status == FEASIBLE:
            return ReturnCode.OK
        if self.status == TIMEOUT:
            return ReturnCode.SOLVE_TIME_LIMIT
        if not self.items:
            return ReturnCode.CONFLICT_UNIDENTIFIED
        return ReturnCode.CONFLICT if self.minimal else ReturnCode.CONFLICT_MUS_TIME_LIMIT

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
        """診断結果の日本語の説明

        Returns:
            診断結果の日本語の説明文（`explain(self)` と同じ）。"""
        return explain(self)


@dataclass
class _MusResult:
    """MUS プロセスの結果"""
    indices: list[int]    # 矛盾に関わる soft の添字（昇順）
    minimal: bool         # 最小か。False なら時間制限で打ち切り、事前候補（最小とは限らない）
    startup: float        # プロセスの起動と式の復元にかかった秒数（式の受け渡しを含む。ready の前に止めたら 0）


def _wait_worker(proc: subprocess.Popen[bytes], work: Path, t0: float, budget: float) -> bool:
    """MUS プロセスの終了を待つ。時間制限を超えたら止める

    時間制限は、段階ごとに数える: 起動と式の復元（`STARTUP_LIMIT`）→ 事前候補の求解（`budget` + `CORE_GRACE`）→
    MUS（`budget`）。段階の切り替わりは、プロセスが書く `ready`・`core.pkl` で知る。

    Args:
        proc: MUS プロセス。
        work: 作業ディレクトリ。
        t0: request を書き始めた時刻（`time.perf_counter()`）。
        budget: MUS 1回あたりの時間制限（秒）。

    Returns:
        時間制限を超えて止めたとき `True`。自分で終わったら `False`。"""
    phase, deadline = "start", t0 + STARTUP_LIMIT
    try:
        while proc.poll() is None:
            now = time.perf_counter()
            if phase == "start" and (work / READY_FILE).exists():
                phase, deadline = "core", now + budget + CORE_GRACE
            if phase == "core" and (work / CORE_FILE).exists():
                phase, deadline = "mus", now + budget
            if now > deadline:
                log.info("MUS（別プロセス）が段階「%s」の時間制限で終わらず、止めた", {"start": "起動", "core": "事前候補", "mus": "MUS"}[phase], stacklevel=5)
                proc.kill()
                return True
            time.sleep(_POLL)
        return False
    finally:
        if proc.poll() is None:                                     # 例外（Ctrl-C など）で抜けたときも、子を残さない
            proc.kill()
        proc.wait()


def _mus_in_worker(soft: list[Expression], hard: list[Expression], solver: str, budget: float) -> _MusResult | None:
    """MUS を別プロセス（`nurse.mus_worker`）で求める。時間内に終われば最小、止めたら事前候補を返す

    式は pickle で渡し（hard と soft を 1 つのオブジェクトにまとめる）、結果は soft の添字で受け取る。
    `fork` を使わず、新しいインタープリターを起動する（`python -O` の `assert` 無効化を避けるため `PYTHONOPTIMIZE` は引き継がない）。

    Args:
        soft: 絞り込み対象の制約式（soft）のリスト。
        hard: 常に成り立たせる制約式（hard）のリスト。
        solver: MUS を求めるソルバー名。
        budget: 時間制限（秒）。事前候補の求解と、MUS の計算に、それぞれ適用する。

    Returns:
        `_MusResult`。事前候補も時間内に取れなかったときは `None`（矛盾は確定しているが、原因は特定できない）。

    Raises:
        SolverUnavailableError: ソルバーが使えない（exact 未導入など）。
        InternalError: MUS プロセスの失敗（モデルが充足可能、結果を返さずに終了、応答が不正など）。"""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONOPTIMIZE"}
    root = str(Path(__file__).resolve().parent.parent)               # nurse パッケージを探せるようにする（インストールの有無によらず）
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [root, env.get("PYTHONPATH")]))
    log.info("MUS（別プロセス）を開始: soft %d 件・hard %d 件・solver=%s・時間制限 %gs", len(soft), len(hard), solver, budget, stacklevel=4)
    with tempfile.TemporaryDirectory(prefix="nurse-mus-") as tmp:     # 作成者だけが読み書きできる（pickle の信頼境界）
        work = Path(tmp)
        wall0, t0 = time.time(), time.perf_counter()
        write_message(work / REQUEST_FILE, make_request(hard, soft, solver, budget))
        t_dump = time.perf_counter() - t0
        with open(work / "stderr.txt", "wb") as err:
            proc = subprocess.Popen([sys.executable, "-m", "nurse.mus_worker", str(work)], env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err)
            killed = _wait_worker(proc, work, t0, budget)
        ready = work / READY_FILE
        startup = ready.stat().st_mtime - wall0 if ready.exists() else 0.0
        log.info("MUS（別プロセス）: 式の書き出し %.2fs、起動と復元 %.2fs（合計 %.2fs で終了）", t_dump, startup, time.perf_counter() - t0, stacklevel=4)
        try:
            if killed:
                if not (work / CORE_FILE).exists():
                    log.info("MUS: 事前候補も取れなかった", stacklevel=4)
                    return None
                core = read_message(work / CORE_FILE, CORE_KEYS, "core")
                log.info("MUS: 時間切れ。事前候補（コア）%d 件を採用（最小でない可能性）", len(core["core_indices"]), stacklevel=4)
                return _MusResult(core["core_indices"], False, startup)
            if not (work / RESPONSE_FILE).exists():
                raise InternalError(f"MUS のプロセスが結果を返さずに終了した（終了コード {proc.returncode}）")
            resp = read_message(work / RESPONSE_FILE, RESPONSE_KEYS, "response")
            if (work / CORE_FILE).exists():
                log.info("MUS の事前候補（コア）: %d 件", len(read_message(work / CORE_FILE, CORE_KEYS, "core")["core_indices"]), stacklevel=4)
        except ProtocolError as e:
            raise InternalError(f"MUS のプロセスの応答が不正: {e}") from None
    status, t = resp["status"], resp["timings"]
    log.info("MUS（別プロセス）の結果: %s（事前候補の求解 %.2fs・MUS %.2fs）", status, t.get("core", 0.0), t.get("mus", 0.0), stacklevel=4)
    if status == MUS:
        return _MusResult(resp["mus_indices"], True, startup)
    if status == UNKNOWN:
        return None                                                  # 事前候補が時間内に取れなかった
    if status == SAT:
        raise InternalError("MUS: 矛盾するはずのモデルが充足可能だった")
    if status == UNAVAILABLE:
        raise SolverUnavailableError(resp["message"])
    raise InternalError(f"MUS の計算が失敗した: {resp['message']}")


def _check(exprs: list, time_limit: float) -> str:
    """exprs が充足可能かを求解用のソルバー（CP-SAT）で調べる。"sat" / "unsat" / "unknown"（時間切れ）

    Args:
        exprs: 調べる制約式のリスト。
        time_limit: 時間制限（秒）。

    Returns:
        `"sat"`（解あり）・`"unsat"`（解なしと証明）・`"unknown"`（時間切れ）のいずれか。"""
    s = cp.SolverLookup.get(SOLVE_SOLVER, cp.Model(exprs))
    attach_engine_log(SOLVE_SOLVER, s)
    if s.solve(time_limit=time_limit):
        return "sat"
    return "unsat" if s.status().exitstatus.name == "UNSATISFIABLE" else "unknown"


def diagnose(I: Instance, eps: Eps, time_limit: float | None = None, mus_solver: str = MUS_SOLVER,
             mus_time_limit: float | None = None) -> Diagnosis:
    """解けるか調べ、解けなければ原因（時間切れ / 制約矛盾）を返す。

    手順:

    1. 通常のモデルを求解用のソルバー（CP-SAT）で解く。解ければ FEASIBLE、時間切れなら TIMEOUT（矛盾かどうかは不明）。
    2. 実行不能と確定したときだけ MUS を求める（指示変数つきのモデルは桁違いに遅いので、解ける問題では解かない）:
        - 背景と前提（カーネル・層1）だけで矛盾するなら、前提の中から MUS を求める（`hard_only=True`。入力データと法令の矛盾）。
        - そうでなければ、前提を hard として固定し、soft（層2・層3）の MUS を求め、その矛盾に関わる前提（例: 固定割当）を追加で絞り込む。

    MUS は `mus_time_limit` 秒（soft と前提でそれぞれ）で打ち切る。打ち切ると、事前候補（最小とは限らない矛盾の集合）を
    返し、`minimal=False` にする。

    Args:
        I: 入力データ。
        eps: ε 制約（品質要件）の閾値。
        time_limit: 通常の求解の時間制限（秒）。省略すると nurse.properties の `solve.time_limit`（既定30秒）。
        mus_solver: MUS を求めるソルバー。既定は `MUS_SOLVER`（exact）。
        mus_time_limit: MUS 1回あたりの時間制限（秒）。省略すると nurse.properties の `mus.time_limit`（既定30秒）。

    Returns:
        診断結果。リターンコードは `Diagnosis.code`（nurse/codes.py）。"""
    log.info("診断開始: time_limit=%s mus_solver=%s mus_time_limit=%s", time_limit, mus_solver, mus_time_limit)
    d = _diagnose(I, eps, time_limit, mus_solver, mus_time_limit)
    _log_result(d)
    return d


def _log_result(d: Diagnosis) -> None:
    """診断の結果（ステータス・リターンコード・MUS）をログに残す。MUS の個別内容は DEBUG。

    Args:
        d: `diagnose` の結果。"""
    log.info("診断結果: ステータス=%s コード=%s(%d) 状態=%s", d.status, d.code.name, int(d.code), d.detail, stacklevel=2)
    if d.status == CONFLICT:
        log.info("MUS: %d 件（前提 %d・soft %d）、最小=%s、前提だけの矛盾=%s、群=%s、ソルバー=%s",
                 len(d.items), len(d.given_items), len(d.soft_items), d.minimal, d.hard_only, d.groups, d.mus_solver, stacklevel=2)
        for x in d.items:
            log.debug("MUS の要素: %s", x.line, stacklevel=2)
    log.info("診断の所要時間: %s", {k: round(v, 2) for k, v in d.times.items()}, stacklevel=2)




def _diagnose(I: Instance, eps: Eps, time_limit: float | None = None, mus_solver: str = MUS_SOLVER,
             mus_time_limit: float | None = None) -> Diagnosis:
    """`diagnose` の本体（ログの出入口は `diagnose`）。引数と戻り値は `diagnose` と同じ。

    Args:
        I: 入力データ。
        eps: ε 制約（品質要件）の閾値。
        time_limit: 通常の求解の時間制限（秒）。
        mus_solver: MUS を求めるソルバー。
        mus_time_limit: MUS 1回あたりの時間制限（秒）。

    Returns:
        診断結果。"""
    cfg = load_settings()
    time_limit = cfg.solve_time_limit if time_limit is None else time_limit
    mus_time_limit = cfg.mus_time_limit if mus_time_limit is None else mus_time_limit
    first = solve(I, eps, time_limit=time_limit)
    times = {"solve": first.times["solve"]}
    state = first.status.exitstatus.name
    log.info("通常の求解: ok=%s ステータス=%s", first.ok, state, stacklevel=2)
    if first.ok:
        return Diagnosis(FEASIBLE, state, times=times)
    if state != "UNSATISFIABLE":
        return Diagnosis(TIMEOUT, f"{time_limit:g}秒で解も矛盾の証明も得られなかった（{state}）", times=times)

    flat = [x for items in first.model.build_named().values() for x in items]
    background = [x.expr for x in flat if x.hard]
    given = [x for x in flat if not x.hard and REGISTRY[x.group].scope in MANDATORY]
    soft = [x for x in flat if not x.hard and REGISTRY[x.group].scope in TOGGLEABLE]
    solver = mus_solver
    position = {id(x): i for i, x in enumerate(flat)}                   # 定義順（日付順など）で並べる

    def run(pool: list[Named], hard_exprs: list[Expression]) -> tuple[list[Named] | None, bool]:
        """時間制限つきの MUS（別プロセス）→ (pool のうち矛盾に関わるもの, 最小か)。事前候補も取れなければ (None, False)。
        MUS プロセスは pool の添字を返すので、そのまま pool を引く"""
        got = _mus_in_worker([x.expr for x in pool], hard_exprs, solver, mus_time_limit)
        log.info("MUS 終了: %s（対象 %d 件・hard %d 件 → 矛盾 %s 件）", "事前候補も取れず原因を特定できない" if got is None
                 else ("最小" if got.minimal else "時間切れ（事前候補を採用。最小でない可能性）"), len(pool), len(hard_exprs),
                 "-" if got is None else len(got.indices), stacklevel=3)
        if got is None:
            return None, False
        times["mus_startup"] = times.get("mus_startup", 0.0) + got.startup
        return sorted((pool[i] for i in got.indices), key=lambda x: position[id(x)]), got.minimal

    log.info("制約を分類: 背景（hard）%d 本・前提（カーネル・層1）%d 本・soft（層2・層3）%d 本", len(background), len(given), len(soft), stacklevel=2)
    t0 = time.perf_counter()
    given_exprs = [x.expr for x in given]
    verdict = _check(background + given_exprs, time_limit)
    if verdict == "unknown":
        return Diagnosis(TIMEOUT, f"オフにできない制約だけの充足判定が{time_limit:g}秒で終わらなかった", times=times)
    if verdict == "unsat":                                              # a. 前提同士の矛盾（soft を外す余地がない）
        if not given:
            raise InternalError("背景の制約だけで矛盾している（モデルの不具合）")
        items, minimal = run(given, background)
        times["mus_given"] = time.perf_counter() - t0
        return Diagnosis(CONFLICT, state, items or [], True, times, solver, minimal)

    soft_items, minimal = run(soft, background + given_exprs)           # b. soft の MUS（一段階）
    times["mus_soft"] = time.perf_counter() - t0
    if soft_items is None:                                              # 矛盾は確定しているが、原因を特定できなかった
        return Diagnosis(CONFLICT, state, [], False, times, solver, False)
    t1 = time.perf_counter()
    involved: list[Named] = []                                          # soft の矛盾に関わる前提だけに絞る
    if given:
        found, minimal_given = run(given, background + [x.expr for x in soft_items])
        if found is None:                                               # 絞れなければ前提は載せない（soft の説明だけ）
            minimal_given = False
        else:
            involved = found
        minimal = minimal and minimal_given
    times["mus_given"] = time.perf_counter() - t1
    items = sorted(involved + soft_items, key=lambda x: position[id(x)])
    return Diagnosis(CONFLICT, state, items, False, times, solver, minimal)


def explain(d: Diagnosis) -> str:
    """Diagnosis の日本語の説明文

    Args:
        d: `diagnose` の結果。

    Returns:
        診断の日本語の説明文。矛盾があれば、原因の制約（MUS）と対処を含む。"""
    if d.status == FEASIBLE:
        return "【診断】解が見つかりました（制約の矛盾はありません）。"
    if d.status == TIMEOUT:
        return ("【診断】時間切れ: 解も、解が無い証明も得られませんでした（制約矛盾かどうかは不明です）。\n"
                f"  状態: {d.detail}\n"
                "  対処: time_limit を延ばす、規模を小さくする、night_band など求解を重くする条件を見直す、"
                "precheck(I, eps) で需要と供給の単純な不足がないか確かめる。")
    if not d.items:                                                    # 矛盾は確定、原因は特定できなかった（時間制限）
        return ("【診断】制約矛盾: 解が存在しません（証明済み）。ただし、矛盾の原因になっている制約を時間内に特定できませんでした。\n"
                "  対処: mus_time_limit を延ばす、規模を小さくする、制約群を Rules.off で外して絞り込む。")
    lines = ["【診断】制約矛盾: 解が存在しません。次の制約は同時には成り立ちません。"]
    lines.append("  （最小不整合部分集合 MUS: このうち1つでも外せば、残りは矛盾しなくなります）" if d.minimal else
                 "  （時間制限で打ち切ったため、最小でない可能性があります。矛盾する集合ですが、不要な制約を含むことがあります）")
    lines.append("")
    lines.append(f"■ 関係する制約群（{len(d.groups)}群）")
    for g in d.groups:
        sp = REGISTRY[g]
        src = f" 根拠: {', '.join(sp.source)}" if sp.source else ""
        lines.append(f"  - {g}: {sp.title or g}（{SCOPE_NAMES[sp.scope]}）{src}")

    def block(title: str, items: list[Named]) -> None:
        lines.extend(["", f"■ {title}（{len(items)}件）"])
        lines.extend(f"  {x.line}" for x in items)             # `{ID}  [{クラス名}] {メッセージ}`。ID は一意なので集約しない

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
