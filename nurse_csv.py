"""
CSV ファイルで入出力するエンジンの窓口（Web フロントとエンジンの間の IF。ファイルだけで受け渡す）。

入力ディレクトリ（すべて UTF-8。Excel で開ける BOM 付きも読める）:

| ファイル | 内容 |
|---|---|
| `shifts.csv` | 勤務の定義（勤務名・開始・終了・区分・勤務扱い・固定専用）。時刻は `HH:MM`（30分単位。翌日にかかる終了は `25:30` のように24時を超えて書く） |
| `nurses.csv` | 看護師（氏名・取れる勤務・公休数・夜勤リーダー資格・夜勤専従・短時間正職員・算入閾値） |
| `history.csv` | 前月末の勤務（氏名と、前月末の日ごとの勤務名） |
| `demand.csv` | 曜日ごとの需要（曜日・開始・終了・必要人数。各曜日を 00:00〜24:00 まで空きなく覆う） |
| `fixed.csv` | 固定割当（氏名・日付・勤務。確定休・有休） |
| `requests.csv` | 勤務者からの要望（氏名・日付・種別[休み / シフト]・勤務） |
| `params.csv` | 計画期間・層ごとのパラメータ・ε 制約・時間制限（キー・値・グループ・説明） |
| `constraints.csv` | 外せる制約（層2・層3）の一覧と、有効（1）/ 無効（0） |

出力ディレクトリ:

| ファイル | 内容 |
|---|---|
| `status.csv` | 結果の状態（`status`: feasible / timeout / conflict / error、リターンコード、説明文、所要時間） |
| `schedule.csv` | 勤務表（解が見つかったとき）。看護師 × 日付 |
| `conflicts.csv` | 矛盾する制約の一覧（実行不可能のとき。区分は `前提`＝オフにできない制約 / `外せる`） |
| `precheck.csv` | 事前検査（需要と供給の単純な不足）の指摘 |

使い方:

```
python nurse_csv.py init DIR [--nurses 30 --days 30 --seed 0]   # 入力 CSV の見本を作る
python nurse_csv.py run IN_DIR OUT_DIR [--log-config パス]       # 読み込み → 求解 → （解けなければ診断）→ 出力 CSV
```

終了コードは `ReturnCode`（nurse/codes.py）。入力の誤りは 30 で、`status.csv` に原因を書く。
"""
from __future__ import annotations
import argparse
import csv
import logging
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import numpy as np
from nurse import (Instance, ShiftDef, Eps, Rules, LawParams, DomainParams, GuidelineParams, REGISTRY, TOGGLEABLE,
                   NurseError, InputError, ReturnCode, code_of, precheck, solve, diagnose, synthetic, setup_logging,
                   OFF_NAME, DAY, HOLIDAY_WEEKLY, HOLIDAY_4W4D)
from nurse.diagnosis import SCOPE_NAMES
from verify import extract

log = logging.getLogger("nurse.csv")

SHIFTS, NURSES, HISTORY, DEMAND, FIXED, REQUESTS, PARAMS, CONSTRAINTS = (
    "shifts.csv", "nurses.csv", "history.csv", "demand.csv", "fixed.csv", "requests.csv", "params.csv", "constraints.csv")
INPUT_FILES = (SHIFTS, NURSES, HISTORY, DEMAND, FIXED, REQUESTS, PARAMS, CONSTRAINTS)
"""入力 CSV のファイル名（読み込む順）。"""
STATUS, SCHEDULE, CONFLICTS, PRECHECK = "status.csv", "schedule.csv", "conflicts.csv", "precheck.csv"
OUTPUT_FILES = (STATUS, SCHEDULE, CONFLICTS, PRECHECK)
"""出力 CSV のファイル名。"""

HEADERS = {
    SHIFTS: ["勤務名", "開始", "終了", "区分", "勤務扱い", "固定専用"],
    NURSES: ["氏名", "取れる勤務", "公休数", "夜勤リーダー資格", "夜勤専従", "短時間正職員", "算入閾値(時間)"],
    DEMAND: ["曜日", "開始", "終了", "必要人数"],
    FIXED: ["氏名", "日付", "勤務"],
    REQUESTS: ["氏名", "日付", "種別", "勤務"],
    PARAMS: ["キー", "値", "グループ", "説明"],
    CONSTRAINTS: ["制約名", "層", "制約クラス", "根拠", "有効"],
}
"""入力 CSV の見出し（`history.csv` は前月末の日数で列が変わるので別扱い）。"""
WEEKDAYS = "月火水木金土日"
KIND_OFF, KIND_SHIFT = "休み", "シフト"
SEP = "|"


# =============================================================================
# パラメータの定義（params.csv の見本・説明・既定値）
# =============================================================================
@dataclass(frozen=True)
class ParamDef:
    """params.csv の1行の定義"""
    key: str            # キー
    default: str        # 既定値（文字列）
    group: str          # グループ
    doc: str            # 説明


PARAM_DEFS: tuple[ParamDef, ...] = (
    ParamDef("start_date", "", "計画", "計画の初日（YYYY-MM-DD）。診断メッセージの日付表記と曜日の基準"),
    ParamDef("n_days", "30", "計画", "計画日数"),
    ParamDef("night_avg", "7", "計画", "夜勤回数の平均（夜勤回数の帯の中心）"),
    ParamDef("leader_cats", "E|N", "計画", "夜勤リーダー（資格者）が必須の勤務区分。`|` で区切る"),
    ParamDef("night_cats", "E|N", "計画", "夜勤回数に数える勤務区分。`|` で区切る"),
    ParamDef("holiday_rule", HOLIDAY_WEEKLY, "層1 労働法規", f"休日の与え方。{HOLIDAY_WEEKLY}=毎週1回（労基法35条1項）/ {HOLIDAY_4W4D}=4週4日以上（同2項）"),
    ParamDef("week_start", "0", "層1 労働法規", "週の起算曜日（0=月 … 6=日）。就業規則で特定する"),
    ParamDef("four_week_origin", "0", "層1 労働法規", "4週の起算日（計画の何日目か。0=初日）"),
    ParamDef("holiday_strict_calendar", "0", "層1 労働法規", "1なら、前日の勤務が日をまたいで食い込む休は休日に数えない"),
    ParamDef("night_window_start", "16:00", "層2 病院共通", "夜勤時間帯の開始"),
    ParamDef("night_window_end", "32:00", "層2 病院共通", "夜勤時間帯の終了（翌8:00 は 32:00）"),
    ParamDef("limit_hours", "72", "層2 病院共通", "夜勤の月平均時間の上限 Λ（時間。72時間ルール）"),
    ParamDef("budget_units", "0", "層2 病院共通", "72時間ルールの超過の配分枠 q（届出単位）"),
    ParamDef("admission_class", "7:1", "層2 病院共通", "入院基本料の区分（7:1 / 10:1 / other）。72時間ルールの算入閾値が変わる"),
    ParamDef("short_time_hours", "12", "層2 病院共通", "短時間正職員の算入閾値（時間）"),
    ParamDef("min_rest_hours", "11", "層3 努力義務", "勤務間隔の下限（時間）"),
    ParamDef("max_consec_work", "5", "層3 努力義務", "連続勤務日数の上限"),
    ParamDef("min_off_block", "1", "層3 努力義務", "休みの最小連続日数（2以上で単発休を禁止）"),
    ParamDef("max_work_weekends", "3", "層3 努力義務", "勤務する週末の数の上限"),
    ParamDef("night_delta", "1", "ε制約", "夜勤回数の帯の幅 δ（帯は [⌊平均⌋−δ, ⌈平均⌉+δ]）"),
    ParamDef("weekend_spread_regular", "2", "ε制約", "土日勤務日数の最大−最小の上限（正規職員）"),
    ParamDef("weekend_spread_part", "2", "ε制約", "同（パート職員）"),
    ParamDef("req_viol_ratio", "0.3", "ε制約", "休み要望・シフト要望それぞれで許容する違反の割合（0〜1）"),
    ParamDef("solve_time_limit", "30", "実行", "求解の時間制限（秒）"),
    ParamDef("mus_time_limit", "30", "実行", "矛盾の原因の絞り込み（MUS）1回あたりの時間制限（秒）"),
)
"""params.csv に書ける項目（これ以外のキーは `InputError`）。"""


# =============================================================================
# CSV の読み書き
# =============================================================================
def read_table(path: Path) -> tuple[list[str], list[list[str]]]:
    """CSV を読む（UTF-8 / BOM 付き UTF-8）。空行は読み飛ばす。

    Args:
        path: CSV ファイルのパス。

    Returns:
        `(見出し, 行のリスト)`。各行の長さは見出しに揃える（足りない分は空文字、余りは `InputError`）。"""
    if not path.exists():
        raise InputError(f"{path.name}: ファイルがない")
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
    if not rows:
        raise InputError(f"{path.name}: 見出し行がない")
    header = [c.strip() for c in rows[0]]
    body = []
    for i, r in enumerate(rows[1:], start=2):
        if len(r) > len(header) and any(c.strip() for c in r[len(header):]):
            raise InputError(f"{path.name}: {i}行目の列数 {len(r)} が見出し {len(header)} を超えている")
        body.append([c.strip() for c in r[:len(header)]] + [""] * (len(header) - len(r)))
    return header, body


def write_table(path: Path, header: Sequence[object], rows: Sequence[Sequence[object]]) -> None:
    """CSV を書く（Excel でそのまま開けるよう BOM 付き UTF-8）。

    Args:
        path: 書き込み先。
        header: 見出し。
        rows: 行のリスト。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _records(path: Path, expected: list[str]) -> list[dict[str, str]]:
    """見出しを確かめて、行を {見出し: 値} にする

    Args:
        path: CSV ファイルのパス。
        expected: 必要な見出し（順不同。これ以外の列は無視）。

    Returns:
        行ごとの辞書のリスト。"""
    header, rows = read_table(path)
    missing = [h for h in expected if h not in header]
    if missing:
        raise InputError(f"{path.name}: 列がない: {missing}（見出し: {header}）")
    return [dict(zip(header, r)) for r in rows]


def parse_time(text: str, where: str) -> int:
    """`HH:MM`（30分単位）→ スロット数。24時を超える表記（翌日）も可。

    Args:
        text: 時刻の文字列。
        where: 誤りのとき示す場所（ファイル名と行など）。

    Returns:
        30分スロット数（0:00 が 0、24:00 が 48）。"""
    m = re.fullmatch(r"(\d{1,2}):(00|30)", text.strip())
    if not m:
        raise InputError(f"{where}: 時刻は 30分単位の HH:MM（例 08:30、翌日にかかる終了は 25:30）: {text!r}")
    return int(m.group(1)) * 2 + int(m.group(2)) // 30


def format_time(slot: int) -> str:
    """スロット数 → `HH:MM`

    Args:
        slot: 30分スロット数。

    Returns:
        `HH:MM` 表記。"""
    return f"{slot // 2:02d}:{slot % 2 * 30:02d}"


def _flag(text: str, where: str) -> bool:
    """1/0（true/false も可）→ bool

    Args:
        text: 値の文字列。空は 0 とみなす。
        where: 誤りのとき示す場所。

    Returns:
        真偽値。"""
    t = text.strip().lower()
    if t in ("1", "true", "yes", "はい"):
        return True
    if t in ("", "0", "false", "no", "いいえ"):
        return False
    raise InputError(f"{where}: 1 か 0 で書く: {text!r}")


def _number(text: str, where: str, cast: type = int, lo: float | None = None, hi: float | None = None) -> float:
    """数値の読み取りと範囲の検査

    Args:
        text: 値の文字列。
        where: 誤りのとき示す場所。
        cast: `int` か `float`。
        lo: 下限（None=なし）。
        hi: 上限（None=なし）。

    Returns:
        数値。"""
    try:
        v = cast(text.strip())
    except ValueError:
        raise InputError(f"{where}: {'整数' if cast is int else '数値'}で書く: {text!r}") from None
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise InputError(f"{where}: {lo}〜{hi} の範囲で書く: {v}")
    return v


# =============================================================================
# 入力の読み込み
# =============================================================================
@dataclass
class Loaded:
    """読み込んだ入力一式"""
    instance: Instance          # 解く問題
    eps: Eps                    # ε 制約の閾値
    solve_time_limit: float     # 求解の時間制限（秒）
    mus_time_limit: float       # MUS 1回あたりの時間制限（秒）


def _params(in_dir: Path) -> dict[str, str]:
    """params.csv を {キー: 値} にする（未記載のキーは既定値、未知のキーは `InputError`）

    Args:
        in_dir: 入力ディレクトリ。

    Returns:
        キーごとの値の文字列。"""
    known = {p.key: p for p in PARAM_DEFS}
    out = {p.key: p.default for p in PARAM_DEFS}
    for i, r in enumerate(_records(in_dir / PARAMS, ["キー", "値"]), start=2):
        key = r["キー"]
        if key not in known:
            raise InputError(f"{PARAMS}: {i}行目: 未知のキー {key!r}（使えるキー: {', '.join(known)}）")
        out[key] = r["値"]
    return out


def load_input(in_dir: str | Path) -> Loaded:
    """入力ディレクトリの CSV を読み、`Instance` と `Eps` を作る。誤りは `InputError`（ファイル名・行つき）。

    Args:
        in_dir: 入力ディレクトリ（`INPUT_FILES` を含む）。

    Returns:
        読み込んだ入力一式。"""
    d = Path(in_dir)
    log.info("入力を読み込み: %s", d)
    P = _params(d)
    start = _date(P["start_date"], f"{PARAMS}: start_date")
    n_days = int(_number(P["n_days"], f"{PARAMS}: n_days", int, 1, 366))

    shifts, sidx = _load_shifts(d)
    nurses = _records(d / NURSES, HEADERS[NURSES][:1] + ["取れる勤務", "公休数"])
    if not nurses:
        raise InputError(f"{NURSES}: 看護師が1人もいない")
    names = [r["氏名"] for r in nurses]
    if len(set(names)) != len(names) or "" in names:
        raise InputError(f"{NURSES}: 氏名は空でなく、重複しない")
    nidx = {nm: i for i, nm in enumerate(names)}

    domain, off_days, seniors, night_only, short_time, incl = [], [], [], [], [], {}
    for i, r in enumerate(nurses):
        where = f"{NURSES}: {r['氏名']}"
        allowed = [x for x in r["取れる勤務"].split(SEP) if x]
        bad = [x for x in allowed if x not in sidx]
        if bad or not allowed:
            raise InputError(f"{where}: 取れる勤務が空か、{SHIFTS} にない勤務名を含む: {bad or '空'}")
        domain.append(sorted({sidx[x] for x in allowed}))
        off_days.append(int(_number(r["公休数"], f"{where} 公休数", int, 0, n_days)))
        if _flag(r.get("夜勤リーダー資格", ""), f"{where} 夜勤リーダー資格"):
            seniors.append(i)
        if _flag(r.get("夜勤専従", ""), f"{where} 夜勤専従"):
            night_only.append(i)
        if _flag(r.get("短時間正職員", ""), f"{where} 短時間正職員"):
            short_time.append(i)
        if r.get("算入閾値(時間)", ""):
            incl[i] = round(_number(r["算入閾値(時間)"], f"{where} 算入閾値(時間)", float, 0, 48) * 2)

    try:
        I = Instance(
            shifts=shifts, domain=domain, n_days=n_days, start_weekday=start.weekday(),
            history=_load_history(d, names, sidx), demand=_load_demand(d, start.weekday(), n_days), seniors=seniors,
            leader_cats=tuple(x for x in P["leader_cats"].split(SEP) if x), night_cats=tuple(x for x in P["night_cats"].split(SEP) if x),
            night_avg=_number(P["night_avg"], f"{PARAMS}: night_avg", float, 0), off_days=off_days,
            fixed=_load_fixed(d, nidx, sidx, start, n_days), rules=_load_rules(d, P),
            night_only=night_only, short_time=short_time, incl_threshold=incl,
            start_date=start, nurse_names=names)
    except InputError as e:
        raise InputError(f"入力データの整合: {e}") from None
    I.req_off, I.req_shift = _load_requests(d, nidx, sidx, start, n_days)
    eps = Eps(night_delta=int(_number(P["night_delta"], f"{PARAMS}: night_delta", int, 0)),
              weekend_spread_regular=int(_number(P["weekend_spread_regular"], f"{PARAMS}: weekend_spread_regular", int, 0)),
              weekend_spread_part=int(_number(P["weekend_spread_part"], f"{PARAMS}: weekend_spread_part", int, 0)),
              req_viol_ratio=_number(P["req_viol_ratio"], f"{PARAMS}: req_viol_ratio", float, 0, 1))
    log.info("入力を読み込み 完了: 看護師 %d 人・%d 日・勤務 %d 種", I.n_nurses, I.n_days, len(shifts))
    return Loaded(I, eps, _number(P["solve_time_limit"], f"{PARAMS}: solve_time_limit", float, 0.1),
                  _number(P["mus_time_limit"], f"{PARAMS}: mus_time_limit", float, 0.1))


def _date(text: str, where: str) -> date:
    """YYYY-MM-DD → date

    Args:
        text: 日付の文字列。
        where: 誤りのとき示す場所。

    Returns:
        日付。"""
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise InputError(f"{where}: 日付は YYYY-MM-DD で書く: {text!r}") from None


def _load_shifts(d: Path) -> tuple[list[ShiftDef], dict[str, int]]:
    """shifts.csv → 勤務の定義と {勤務名: 添字}

    Args:
        d: 入力ディレクトリ。

    Returns:
        `(ShiftDef のリスト, 勤務名 → 添字)`。"""
    shifts: list[ShiftDef] = []
    for i, r in enumerate(_records(d / SHIFTS, HEADERS[SHIFTS]), start=2):
        where = f"{SHIFTS}: {i}行目"
        s, e = parse_time(r["開始"], where), parse_time(r["終了"], where)
        is_work = _flag(r["勤務扱い"], f"{where} 勤務扱い")
        if is_work and not (0 <= s < DAY and s < e <= 2 * DAY):
            raise InputError(f"{where}: 勤務は 開始 < 終了（翌日にかかる場合 48 スロット=24:00 を超えて書く。最大 48:00）で、開始は当日（00:00〜23:30）: {r['開始']}〜{r['終了']}")
        if not r["勤務名"] or not r["区分"]:
            raise InputError(f"{where}: 勤務名と区分は必須")
        shifts.append(ShiftDef(r["勤務名"], s, e, r["区分"], is_work, _flag(r["固定専用"], f"{where} 固定専用")))
    names = [s.name for s in shifts]
    if len(set(names)) != len(names):
        raise InputError(f"{SHIFTS}: 勤務名が重複: {sorted({x for x in names if names.count(x) > 1})}")
    if OFF_NAME not in names:
        raise InputError(f"{SHIFTS}: 公休の勤務 {OFF_NAME!r} が必要")
    return shifts, {nm: i for i, nm in enumerate(names)}


def _load_history(d: Path, names: list[str], sidx: dict[str, int]) -> np.ndarray:
    """history.csv → 履歴の配列（看護師 × 前月末の日数）

    Args:
        d: 入力ディレクトリ。
        names: 看護師の氏名（nurses.csv の順）。
        sidx: 勤務名 → 添字。

    Returns:
        履歴の配列。"""
    header, rows = read_table(d / HISTORY)
    if header[0] != "氏名" or len(header) < 2:
        raise InputError(f"{HISTORY}: 先頭の列は「氏名」、続けて前月末の日ごとの列が必要（見出し: {header}）")
    by_name = {r[0]: r[1:] for r in rows}
    missing = [nm for nm in names if nm not in by_name]
    extra = [nm for nm in by_name if nm not in names]
    if missing or extra:
        raise InputError(f"{HISTORY}: 氏名が {NURSES} と一致しない（足りない: {missing}、余分: {extra}）")
    out = []
    for nm in names:
        bad = [x for x in by_name[nm] if x not in sidx]
        if bad:
            raise InputError(f"{HISTORY}: {nm}: {SHIFTS} にない勤務名（空欄を含む）: {bad}")
        out.append([sidx[x] for x in by_name[nm]])
    return np.array(out, dtype=int)


def _load_demand(d: Path, start_weekday: int, n_days: int) -> list[list[tuple[int, int, int]]]:
    """demand.csv（曜日ごとの需要）→ 計画日ごとの需要区間

    Args:
        d: 入力ディレクトリ。
        start_weekday: 計画初日の曜日（0=月）。
        n_days: 計画日数。

    Returns:
        日ごとの `(開始, 終了, 必要人数)` のリスト。"""
    by_wd: dict[int, list[tuple[int, int, int]]] = {}
    for i, r in enumerate(_records(d / DEMAND, HEADERS[DEMAND]), start=2):
        where = f"{DEMAND}: {i}行目"
        if len(r["曜日"]) != 1 or r["曜日"] not in WEEKDAYS:
            raise InputError(f"{where}: 曜日は 月火水木金土日 の1文字: {r['曜日']!r}")
        a, b = parse_time(r["開始"], where), parse_time(r["終了"], where)
        by_wd.setdefault(WEEKDAYS.index(r["曜日"]), []).append((a, b, int(_number(r["必要人数"], f"{where} 必要人数", int, 0))))
    out = []
    for day in range(n_days):
        wd = (start_weekday + day) % 7
        if wd not in by_wd:
            raise InputError(f"{DEMAND}: {WEEKDAYS[wd]}曜日の需要がない（00:00〜24:00 を覆う行が必要。需要なしは必要人数 0 で書く）")
        out.append(sorted(by_wd[wd]))
    return out


def _day_index(text: str, start: date, n_days: int, where: str) -> int:
    """日付 → 計画日の添字（0=初日）。計画期間の外は `InputError`

    Args:
        text: 日付の文字列。
        start: 計画の初日。
        n_days: 計画日数。
        where: 誤りのとき示す場所。

    Returns:
        計画日の添字。"""
    day = (_date(text, where) - start).days
    if not 0 <= day < n_days:
        raise InputError(f"{where}: 日付 {text} が計画期間（{start}〜{start + timedelta(days=n_days - 1)}）の外")
    return day


def _load_fixed(d: Path, nidx: dict[str, int], sidx: dict[str, int], start: date, n_days: int) -> dict[tuple[int, int], int]:
    """fixed.csv → 固定割当 {(看護師, 日): 勤務}

    Args:
        d: 入力ディレクトリ。
        nidx: 氏名 → 看護師の添字。
        sidx: 勤務名 → 添字。
        start: 計画の初日。
        n_days: 計画日数。

    Returns:
        固定割当。"""
    out: dict[tuple[int, int], int] = {}
    for i, r in enumerate(_records(d / FIXED, HEADERS[FIXED]), start=2):
        where = f"{FIXED}: {i}行目"
        if r["氏名"] not in nidx or r["勤務"] not in sidx:
            raise InputError(f"{where}: {NURSES} か {SHIFTS} にない氏名・勤務名: {r['氏名']!r} {r['勤務']!r}")
        key = (nidx[r["氏名"]], _day_index(r["日付"], start, n_days, where))
        if key in out and out[key] != sidx[r["勤務"]]:
            raise InputError(f"{where}: 同じ人・同じ日に別の勤務が固定されている")
        out[key] = sidx[r["勤務"]]
    return out


def _load_requests(d: Path, nidx: dict[str, int], sidx: dict[str, int], start: date,
                   n_days: int) -> tuple[dict[int, list[int]], dict[int, list[tuple[int, int]]]]:
    """requests.csv → 休み要望・シフト要望

    Args:
        d: 入力ディレクトリ。
        nidx: 氏名 → 看護師の添字。
        sidx: 勤務名 → 添字。
        start: 計画の初日。
        n_days: 計画日数。

    Returns:
        `(休み要望 {看護師: [日]}, シフト要望 {看護師: [(日, 勤務)]})`。"""
    req_off: dict[int, list[int]] = {}
    req_shift: dict[int, list[tuple[int, int]]] = {}
    for i, r in enumerate(_records(d / REQUESTS, HEADERS[REQUESTS]), start=2):
        where = f"{REQUESTS}: {i}行目"
        if r["氏名"] not in nidx:
            raise InputError(f"{where}: {NURSES} にない氏名: {r['氏名']!r}")
        n, day = nidx[r["氏名"]], _day_index(r["日付"], start, n_days, where)
        if r["種別"] == KIND_OFF:
            req_off.setdefault(n, []).append(day)
        elif r["種別"] == KIND_SHIFT:
            if r["勤務"] not in sidx:
                raise InputError(f"{where}: シフト要望の勤務名が {SHIFTS} にない: {r['勤務']!r}")
            req_shift.setdefault(n, []).append((day, sidx[r["勤務"]]))
        else:
            raise InputError(f"{where}: 種別は「{KIND_OFF}」か「{KIND_SHIFT}」: {r['種別']!r}")
    return req_off, req_shift


def _load_rules(d: Path, P: dict[str, str]) -> Rules:
    """params.csv と constraints.csv → `Rules`（層ごとのパラメータとオフにする制約）

    Args:
        d: 入力ディレクトリ。
        P: params.csv の {キー: 値}。

    Returns:
        層ごとのパラメータ。"""
    w = lambda k: f"{PARAMS}: {k}"
    hours = lambda k, hi=48: round(_number(P[k], w(k), float, 0, hi) * 2)
    off = set()
    for i, r in enumerate(_records(d / CONSTRAINTS, ["制約名", "有効"]), start=2):
        name = r["制約名"]
        if name not in REGISTRY or name not in TOGGLEABLE_NAMES:
            raise InputError(f"{CONSTRAINTS}: {i}行目: 外せる制約にない名前 {name!r}")
        if not _flag(r["有効"], f"{CONSTRAINTS}: {i}行目 有効"):
            off.add(name)
    try:
        return _rules(P, hours, w, off)
    except InputError as e:
        raise InputError(f"{PARAMS}: {e}") from None


def _rules(P: dict[str, str], hours: Callable[..., int], w: Callable[[str], str], off: set[str]) -> Rules:
    """params.csv の値から `Rules` を作る（値の範囲の検査は各パラメータのクラスが行う）

    Args:
        P: params.csv の {キー: 値}。
        hours: 時間（時間単位の値）→ スロット数。
        w: キー → 誤りのとき示す場所。
        off: オフにする制約名。

    Returns:
        層ごとのパラメータ。"""
    return Rules(
        law=LawParams(holiday_rule=P["holiday_rule"], week_start=int(_number(P["week_start"], w("week_start"), int, 0, 6)),
                      four_week_origin=int(_number(P["four_week_origin"], w("four_week_origin"), int, 0)),
                      holiday_strict_calendar=_flag(P["holiday_strict_calendar"], w("holiday_strict_calendar"))),
        domain=DomainParams(night_window=(parse_time(P["night_window_start"], w("night_window_start")),
                                          parse_time(P["night_window_end"], w("night_window_end"))),
                            limit_units=hours("limit_hours", 168), budget_units=int(_number(P["budget_units"], w("budget_units"), int, 0)),
                            admission_class=P["admission_class"], short_time_slots=hours("short_time_hours")),
        guideline=GuidelineParams(min_rest=hours("min_rest_hours"), max_consec_work=int(_number(P["max_consec_work"], w("max_consec_work"), int, 1)),
                                  min_off_block=int(_number(P["min_off_block"], w("min_off_block"), int, 1)),
                                  max_work_weekends=int(_number(P["max_work_weekends"], w("max_work_weekends"), int, 0))),
        off=frozenset(off))


TOGGLEABLE_NAMES = frozenset(nm for nm, sp in REGISTRY.items() if sp.scope in ("domain", "optional"))
"""オフにできる制約名（層2・層3）。"""


# =============================================================================
# 入力 CSV の見本（init）
# =============================================================================
def write_sample(out_dir: str | Path, n_nurses: int = 30, n_days: int = 30, seed: int = 0) -> None:
    """合成データから入力 CSV の見本を作る（看護師数は常勤:パート = 26:4 の比）。

    Args:
        out_dir: 書き込み先ディレクトリ。
        n_nurses: 看護師数（常勤とパートの合計。4人以上）。
        n_days: 計画日数。
        seed: 乱数の種（確定休・休み要望）。"""
    if n_nurses < 4:
        raise InputError(f"看護師数は 4 人以上: {n_nurses}")
    out = Path(out_dir)
    n_part = max(1, round(n_nurses * 4 / 30))
    n_full = n_nurses - n_part
    today = date.today()
    start = today + timedelta(days=(7 - today.weekday()) % 7 or 7)          # 次の月曜日
    I = synthetic(n_full, n_part, T=n_days, seed=seed, start_date=start)
    names = [f"看護師{i + 1:02d}" for i in range(I.n_nurses)]
    sname = [s.name for s in I.shifts]
    write_table(out / SHIFTS, HEADERS[SHIFTS], [[s.name, format_time(s.start), format_time(s.end), s.cat, int(s.is_work), int(s.fixed_only)]
                                                 for s in I.shifts])
    write_table(out / NURSES, HEADERS[NURSES], [
        [names[n], SEP.join(sname[s] for s in I.domain[n]), I.off_days[n], int(n in I.seniors), int(n in I.night_only), int(n in I.short_time),
         "" if n not in I.incl_threshold else I.incl_threshold[n] / 2] for n in range(I.n_nurses)])
    write_table(out / HISTORY, ["氏名"] + [f"前月末-{I.n_history - k}" for k in range(I.n_history)],
                [[names[n]] + [sname[s] for s in I.history[n]] for n in range(I.n_nurses)])
    write_table(out / DEMAND, HEADERS[DEMAND], [[WEEKDAYS[wd], format_time(a), format_time(b), r]
                                                 for wd in range(7) for a, b, r in I.demand[wd]])
    day_str = lambda d: (start + timedelta(days=d)).isoformat()
    write_table(out / FIXED, HEADERS[FIXED], [[names[n], day_str(d), sname[s]] for (n, d), s in sorted(I.fixed.items())])
    write_table(out / REQUESTS, HEADERS[REQUESTS], [[names[n], day_str(d), KIND_OFF, ""] for n, ds in sorted(I.req_off.items()) for d in ds]
                + [[names[n], day_str(d), KIND_SHIFT, sname[s]] for n, ds in sorted(I.req_shift.items()) for d, s in ds])
    defaults = {p.key: p.default for p in PARAM_DEFS}
    defaults.update(start_date=start.isoformat(), n_days=str(n_days), night_avg=f"{I.night_avg:g}")
    write_table(out / PARAMS, HEADERS[PARAMS], [[p.key, defaults[p.key], p.group, p.doc] for p in PARAM_DEFS])
    write_table(out / CONSTRAINTS, HEADERS[CONSTRAINTS], [[nm, SCOPE_NAMES[sp.scope], sp.title, SEP.join(sp.source), 1]
                                                           for nm, sp in REGISTRY.items() if nm in TOGGLEABLE_NAMES])
    log.info("入力 CSV の見本を作成: %s（看護師 %d 人・%d 日）", out, I.n_nurses, n_days)


# =============================================================================
# 実行と出力
# =============================================================================
def _write_status(out: Path, rows: list[tuple[str, object]]) -> None:
    """status.csv を書く

    Args:
        out: 出力ディレクトリ。
        rows: `(項目, 値)` のリスト。"""
    write_table(out / STATUS, ["項目", "値"], [[k, str(v)] for k, v in rows])


def run_dir(in_dir: str | Path, out_dir: str | Path) -> ReturnCode:
    """入力 CSV を読み、求解し（解けなければ診断して）、出力 CSV を書く。想定した失敗（`NurseError`）も `status.csv` に書く。

    Args:
        in_dir: 入力ディレクトリ。
        out_dir: 出力ディレクトリ（無ければ作る。古い出力は消す）。

    Returns:
        リターンコード。解が見つかれば `OK`。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for f in OUTPUT_FILES:
        (out / f).unlink(missing_ok=True)
    t0 = time.perf_counter()
    try:
        L = load_input(in_dir)
        I, eps = L.instance, L.eps
        issues = precheck(I, eps)
        log.info("事前検査: %s", f"NG {len(issues)} 件" if issues else "OK")
        write_table(out / PRECHECK, ["指摘"], [[x] for x in issues])
        res = solve(I, eps, time_limit=L.solve_time_limit)
        common: list[tuple[str, object]] = [("n_nurses", I.n_nurses), ("n_days", I.n_days), ("start_date", I.start_date)]
        if res.ok:
            A = extract(res.model)
            start, names = I.start_date, I.nurse_names
            assert start is not None and names is not None          # CSV から作った入力は、必ず開始日と氏名を持つ
            days = [(start + timedelta(days=d)).isoformat() for d in range(I.n_days)]
            write_table(out / SCHEDULE, ["氏名"] + days, [[names[n]] + [I.shifts[s].name for s in A[n]] for n in range(I.n_nurses)])
            code, text = ReturnCode.OK, "解が見つかりました。"
            extra: list[tuple[str, object]] = []
        else:
            dg = diagnose(I, eps, time_limit=L.solve_time_limit, mus_time_limit=L.mus_time_limit)
            code, text = dg.code, dg.explain()
            given = {id(x) for x in dg.given_items}
            write_table(out / CONFLICTS, ["区分", "ID", "制約群", "制約クラス", "層", "メッセージ"],
                        [["前提" if id(x) in given else "外せる", x.id, x.group, x.title, SCOPE_NAMES[REGISTRY[x.group].scope], x.message]
                         for x in dg.items])
            extra = [("status", dg.status), ("minimal", int(dg.minimal)), ("hard_only", int(dg.hard_only))]
        status = "feasible" if code == ReturnCode.OK else extra[0][1]
        _write_status(out, [("status", status), ("code", int(code)), ("code_name", code.name), ("label", code.label),
                            ("seconds", f"{time.perf_counter() - t0:.1f}"), *common, ("message", text),
                            *[kv for kv in extra if kv[0] != "status"]])
        return code
    except NurseError as e:
        log.error("エラー: %s", e)
        code = code_of(e)
        _write_status(out, [("status", "error"), ("code", int(code)), ("code_name", code.name), ("label", code.label),
                            ("seconds", f"{time.perf_counter() - t0:.1f}"), ("message", str(e))])
        return code


def main(argv: list[str] | None = None) -> int:
    """コマンドライン。`init` で見本を作り、`run` で求解する。

    Args:
        argv: 引数（省略すると `sys.argv`）。

    Returns:
        終了コード（`run` は `ReturnCode` の整数値）。"""
    ap = argparse.ArgumentParser(description="CSV で入出力するエンジンの窓口")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="入力 CSV の見本を作る")
    p_init.add_argument("dir")
    p_init.add_argument("--nurses", type=int, default=30)
    p_init.add_argument("--days", type=int, default=30)
    p_init.add_argument("--seed", type=int, default=0)
    p_run = sub.add_parser("run", help="入力 CSV を読み、求解して出力 CSV を書く")
    p_run.add_argument("in_dir")
    p_run.add_argument("out_dir")
    p_run.add_argument("--log-config", default=None, help="ログ設定ファイル（YAML / JSON）のパス")
    args = ap.parse_args(argv)
    if args.cmd == "init":
        try:
            write_sample(args.dir, args.nurses, args.days, args.seed)
        except NurseError as e:
            print(f"エラー: {e}", file=sys.stderr)
            return int(code_of(e))
        print(f"入力 CSV の見本を作成しました: {args.dir}")
        return 0
    setup_logging(args.log_config)
    try:
        code = run_dir(args.in_dir, args.out_dir)
    except Exception:
        log.exception("想定外の例外")
        raise
    print(f"終了コード: {int(code)} {code.name} — {code.label}")
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
