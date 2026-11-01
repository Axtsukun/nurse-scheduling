"""入力データ・層ごとのパラメータ・インスタンスへの単純な問い合わせ。
ソルバーにも制約式にも依存しない（このモジュールは cpmpy を import しない）。

時刻: 30分単位の整数。0 = 当日 0:00、48 = 翌日 0:00。勤務は開始する暦日 d に属し、
`[start, end)` が 48 を超えれば翌日にかかる（深夜 0:30 開始は当日の start=1）。

層構成（docs/legal/ と CLAUDE.md を参照）:

- **カーネル**: 変数 x・窓の部品・登録の仕組み（常に有効）
- **層1 law**: 労働法規（オフにできない。値は法令の選択肢）
- **層2 domain**: 病院・看護職共通（診療報酬の施設基準など。病院の判断でオン・オフ・値の調整）
- **層3 optional**: 努力義務・ガイドライン・個別要望（クライアントが選ぶ）

上の層の制約は下の層の制約に「加える」（論理積）ので、下の層を弱めることはない。"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from datetime import date
import numpy as np
from .codes import InputError

DAY = 48                                    # 1日 = 48 スロット（30分）
WEEK = 7                                    # 週の日数
FOUR_WEEKS = 4 * WEEK                       # 4週の日数
OFF_NAME = "休"                              # 公休の勤務名。コードに直書きしてよい唯一の勤務名（CLAUDE.md）
HOLIDAY_WEEKLY = "weekly"                   # 労基法35条1項: 毎週少なくとも1回
HOLIDAY_4W4D = "4w4d"                       # 労基法35条2項: 4週間で4日以上


@dataclass(frozen=True)
class ShiftDef:
    """勤務の値 s ∈ 𝒮 の定義（数式: start_s, end_s, k(s), work_s）"""
    name: str                  # 勤務の名前
    start: int                 # start_s（30分単位, 当日0:00起点）
    end: int                   # end_s（`[start, end)` の end。48 を超えれば翌日にかかる）
    cat: str                   # 区分 k(s): "D"日勤帯 "E"準夜帯 "N"深夜帯 "P"パート "OFF"公休 "LV"有休等
    is_work: bool              # work_s（勤務として数える値か）
    fixed_only: bool = False   # 入力で固定された日にのみ取れる値（有休・研修など）


# =============================================================================
# 層ごとのパラメータ
# =============================================================================
@dataclass(frozen=True)
class LawParams:
    """層1: 労働法規の選択肢（docs/legal/01_law.md）"""
    holiday_rule: str = HOLIDAY_WEEKLY     # 労基法35条 HOLIDAY_WEEKLY（1項）/ HOLIDAY_4W4D（2項）
    week_start: int = 0                    # 週の起算曜日（0=月）。就業規則で特定する
    four_week_origin: int = 0              # 4週の起算日（計画日の添字）
    holiday_strict_calendar: bool = False  # True なら「前日の勤務が日をまたいで食い込む休」は休日に数えない

    def __post_init__(self):
        if self.holiday_rule not in (HOLIDAY_WEEKLY, HOLIDAY_4W4D):
            raise InputError(f"holiday_rule は {HOLIDAY_WEEKLY!r} か {HOLIDAY_4W4D!r}: {self.holiday_rule!r}")
        if not 0 <= self.week_start < WEEK:
            raise InputError(f"week_start は 0〜6（0=月）: {self.week_start}")


@dataclass(frozen=True)
class DomainParams:
    """層2: 病院・看護職共通（診療報酬の施設基準。docs/legal/02_reimbursement.md）"""
    night_window: tuple[int, int] = (32, 64)   # 夜勤時間帯 [w0, w1)（16:00-翌8:00）
    handover: dict[int, int] = field(default_factory=dict)  # 申し送り控除 {s: スロット数}
    limit_units: int = 144                 # Λ = 72h
    budget_units: int = 0                  # q（届出単位での配分枠）
    admission_class: str = "7:1"           # 入院基本料の区分: incl_slots のキー
    # 72時間ルールの算入閾値 θ（スロット数、「未満」は除外）。7対1・10対1は16h、その他は8h
    incl_slots: dict[str, int] = field(default_factory=lambda: {"7:1": 32, "10:1": 32, "other": 16})  # 区分 → 算入閾値 θ（スロット数）
    short_time_slots: int = 24             # 短時間正職員の閾値（12h）

    def __post_init__(self):
        if self.admission_class not in self.incl_slots:
            raise InputError(f"admission_class は {sorted(self.incl_slots)} のいずれか: {self.admission_class!r}")

    def theta(self, short_time: bool) -> int:
        """算入閾値 θ_n（スロット）: 短時間正職員は short_time_slots、それ以外は入院基本料の区分で決まる"""
        return self.short_time_slots if short_time else self.incl_slots[self.admission_class]


@dataclass(frozen=True)
class GuidelineParams:
    """層3: 努力義務・ガイドライン（docs/legal/03_guidelines.md）"""
    min_rest: int = 22                     # 勤務間隔の下限 ρ（11h）
    max_consec_work: int = 5               # K（連続勤務日数の上限）
    min_off_block: int = 1                 # m（2以上で単発休を禁止）
    max_work_weekends: int = 3             # W（勤務する週末の数の上限）


@dataclass(frozen=True)
class Rules:
    """病院ごとのプロファイル。off は層2・層3でオフにする制約名（層1・カーネルは指定不可。検査は registry）"""
    law: LawParams = field(default_factory=LawParams)                    # 層1の選択肢
    domain: DomainParams = field(default_factory=DomainParams)           # 層2のパラメータ
    guideline: GuidelineParams = field(default_factory=GuidelineParams)  # 層3のパラメータ
    off: frozenset[str] = frozenset()                                    # オフにする制約名（層2・層3のみ）


def _delegate(layer: str, attr: str) -> property:
    """Instance から rules.<layer>.<attr> を読む読み取り専用プロパティ"""
    return property(lambda self: getattr(getattr(self.rules, layer), attr),
                    doc=f"rules.{layer}.{attr} への委譲（読み取り専用）")


@dataclass
class Instance:
    """解く問題の入力データ全体。看護師・勤務の値・日数・前月末の履歴・需要・各層のパラメータ・個別要望を持つ。
    制約はここから値を読む。項目ごとの説明と、対応する数式の記号は下の各項目を参照。"""
    shifts: list[ShiftDef]                 # 𝒮（添字 s は list の位置）
    domain: list[list[int]]                # S_n ⊆ 𝒮（看護師 n が取れる値）
    n_days: int                            # 計画日数 T
    start_weekday: int                     # 計画日0の曜日（0=月）
    history: np.ndarray                    # [n, H] 前月末の勤務 h_{n,t}（値は 𝒮 の添字）
    demand: list[list[tuple[int, int, int]]]  # 日 d の需要区間（番）(a, b, r)：[a,b) に r 人以上。番ごとに一定で、1日（0〜48）を空きなく覆う。需要なしは r=0
    seniors: list[int]                     # 夜勤リーダー資格者 L ⊆ 𝒩
    leader_cats: tuple[str, ...]           # リーダー必須の区分
    night_cats: tuple[str, ...]            # 夜勤回数に数える区分 𝒦^night
    night_avg: float                       # 夜勤回数の平均（ε帯の中心）
    off_days: list[int]                    # 公休数 F_n
    fixed: dict[tuple[int, int], int]      # 固定割当 (n, d) → s（確定休・有休）
    soft_off: dict[int, list[int]]         # 希望休 R_n
    rules: Rules = field(default_factory=Rules)   # 層ごとのパラメータ
    # 看護師ごとのデータ（72時間ルール）
    night_only: list[int] = field(default_factory=list)     # 夜勤専従者（計算から除外）
    short_time: list[int] = field(default_factory=list)     # 短時間正職員（算入閾値が rules.domain.short_time_slots）
    incl_threshold: dict[int, int] = field(default_factory=dict)  # θ_n の個別上書き（スロット）
    # 個別要望（ε制約で扱う。違反は eps.req_viol_ratio までの割合で許容）
    req_off: dict[int, list[int]] = field(default_factory=dict)                # 休み要望 n → [d]
    req_shift: dict[int, list[tuple[int, int]]] = field(default_factory=dict)  # シフト要望 n → [(d, s)]
    # 表示方針（診断のメッセージ。docs/design/constraint-message-spec.md）。求解には使わない
    start_date: date | None = None                 # 計画日0の暦の日付。指定すると MM/DD 表記、未指定なら「N日目(曜)」
    nurse_names: list[str] | None = None           # 看護師の表示名（職員名・職員ID）。未指定なら N05
    cat_names: dict[str, str] = field(default_factory=dict)   # 勤務区分名の上書き（未指定の区分は既定の表）

    def __post_init__(self):
        self._check_demand()
        if self.start_date is not None and self.start_date.weekday() != self.start_weekday:
            raise InputError(f"start_date {self.start_date} は曜日が start_weekday={self.start_weekday}（0=月）と一致しない"
                             f"（{self.start_date.weekday()}）")
        if self.nurse_names is not None and len(self.nurse_names) != len(self.domain):
            raise InputError(f"nurse_names の長さ {len(self.nurse_names)} が看護師数 {len(self.domain)} と違う")

    def _check_demand(self) -> None:
        """需要は番（需要区間）ごとに一定の人数で、各日を 0〜DAY の間、空きなく重なりなく覆う（需要なしは r=0 で明示）"""
        if len(self.demand) != self.n_days:
            raise InputError(f"demand の日数 {len(self.demand)} が n_days={self.n_days} と違う")
        for d, bands in enumerate(self.demand):
            pos = 0
            for a, b, r in sorted(bands):
                if a != pos or not a < b or r < 0:
                    raise InputError(f"demand[{d}] は 0〜{DAY} を空きなく重なりなく覆う区間（a<b, r>=0）でなければならない: "
                                     f"{sorted(bands)}（{pos} の位置で不正: ({a}, {b}, {r})）")
                pos = b
            if pos != DAY:
                raise InputError(f"demand[{d}] が {DAY} まで覆っていない（{pos} で終わる）。需要なしの時間帯は r=0 で明示する")

    # 層パラメータへの委譲（制約・検証器・テストはこの名前で参照する）
    min_rest = _delegate("guideline", "min_rest")  # rules.guideline.min_rest への委譲
    max_consec_work = _delegate("guideline", "max_consec_work")  # rules.guideline.max_consec_work への委譲
    min_off_block = _delegate("guideline", "min_off_block")  # rules.guideline.min_off_block への委譲
    max_work_weekends = _delegate("guideline", "max_work_weekends")  # rules.guideline.max_work_weekends への委譲
    night_window = _delegate("domain", "night_window")  # rules.domain.night_window への委譲
    handover = _delegate("domain", "handover")  # rules.domain.handover への委譲
    limit_units = _delegate("domain", "limit_units")  # rules.domain.limit_units への委譲
    budget_units = _delegate("domain", "budget_units")  # rules.domain.budget_units への委譲

    @property
    def n_nurses(self) -> int:
        """看護師数 N"""
        return len(self.domain)

    @property
    def n_history(self) -> int:
        """履歴の日数 H"""
        return self.history.shape[1]

    def idx(self, name: str) -> int:
        """勤務名 → 添字 s。無ければ KeyError"""
        for i, s in enumerate(self.shifts):
            if s.name == name:
                return i
        raise KeyError(f"勤務 {name!r} が shifts にない")


@dataclass
class Eps:
    """ε制約の閾値（緩和ループで調整）。symmetry_break だけは閾値ではなく探索を絞る技法の選択（実験用）"""
    night_delta: int = 1                   # δ: 夜勤回数の帯 [⌊avg⌋−δ, ⌈avg⌉+δ]
    soft_off_viol: int = 10                # κ: 希望休違反数の上限
    weekend_spread_regular: int = 2        # 土日勤務日数の max−min の上限（正規職員）
    weekend_spread_part: int = 2           # 同（パート職員）
    req_viol_ratio: float = 0.3            # 休み要望・シフト要望それぞれで許容する違反割合（違反数 ≤ ⌊ratio×要望数⌋）
    symmetry_break: str = ""               # "" なし / "exact" 完全同種のみ（健全） / "relaxed" 属性が近い者（不完全）


# =============================================================================
# インスタンスへの単純な問い合わせ
# =============================================================================
def inclusion_threshold(I: Instance, n: int) -> int:
    """θ_n: 72時間ルールの算入閾値（スロット）。個別上書き → 短時間正職員 → 入院基本料の区分、の順"""
    if n in I.incl_threshold:
        return I.incl_threshold[n]
    return I.rules.domain.theta(n in I.short_time)


def night_band(I: Instance, eps: Eps) -> tuple[int, int]:
    """夜勤回数の帯 [⌊avg⌋−δ, ⌈avg⌉+δ]（下限は 0 以上）"""
    return max(0, math.floor(I.night_avg) - eps.night_delta), math.ceil(I.night_avg) + eps.night_delta


def night_capable(I: Instance, n: int) -> bool:
    """看護師 n が夜勤区分の勤務を取れるか"""
    return any(I.shifts[s].cat in I.night_cats for s in I.domain[n])


def is_part(I: Instance, n: int) -> bool:
    """パート職員 = 取れる勤務の区分が "P" のみ（休・有休を除く）"""
    return {I.shifts[s].cat for s in I.domain[n] if I.shifts[s].is_work} == {"P"}


def weekend_days(I: Instance) -> tuple[list[int], list[int]]:
    """計画日のうち土曜・日曜の日付 d（start_weekday: 0=月）"""
    wd = lambda d: (I.start_weekday + d) % WEEK
    return ([d for d in range(I.n_days) if wd(d) == 5], [d for d in range(I.n_days) if wd(d) == 6])


def weekend_pairs(I: Instance) -> list[tuple[int, int]]:
    """週末 = (土曜 d, 日曜 d+1) の組。勤務週末数・検証・評価で共通に使う定義。
    計画の最終日が土曜のとき、日曜が計画の外なので、その週末は数えない。"""
    return [(d, d + 1) for d in weekend_days(I)[0] if d + 1 < I.n_days]
