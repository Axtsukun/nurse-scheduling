"""看護師勤務表 CSP エンジン（CPMpy + CP-SAT）。公開 API を集約する。

構成（依存は下向きのみ）
  instance   入力データ・層ごとのパラメータ・単純な問い合わせ
  solution   解（割当表）の読み取り部品
  preprocess 時刻データから係数・集合・窓を作る純Python
  message    制約メッセージの構造化データ（対象・パラメータ）と整形器
  registry   制約の登録（@constraint）と、有効な制約の選択
  kernel     変数 x と式の部品、構造制約（core）
  law / domain / optional   層1 / 層2 / 層3 の制約（ミックスイン）
  model      NurseModel（上の合成）
  solver     求解・勤務表の表示
  diagnosis  解けないときの診断（時間切れ / 制約矛盾 → MUS を日本語で説明）
  synthetic  合成インスタンス
"""
from .instance import (DAY, WEEK, FOUR_WEEKS, OFF_NAME, HOLIDAY_WEEKLY, HOLIDAY_4W4D, ShiftDef, LawParams,
                       DomainParams, GuidelineParams, Rules, Instance, Eps, inclusion_threshold, night_band,
                       night_capable, is_part, weekend_days, weekend_pairs)
from .solution import Assignment, full_sequences, staff_on_duty
from .preprocess import (night_units, rest_cliques, overnight_shifts, holiday_windows, cover_intervals,
                         symmetry_classes, precheck)
from .message import Renderer, Nurse, Day, DaySpan, Span, Cat, Shift, Clock, Param, CAT_NAMES, WEEKDAYS
from .registry import (ConstraintSpec, Item, Aux, Named, REGISTRY, SCOPES, MANDATORY, TOGGLEABLE, constraint,
                       selected_constraints, toggleable)
from .model import NurseModel
from .solver import SOLVE_SOLVER, SolveResult, solve, roster
from .diagnosis import MUS_SOLVER, Diagnosis, FEASIBLE, TIMEOUT, CONFLICT, diagnose, explain
from .synthetic import SIZES, hm, synthetic, synthetic_for, with_requests

__all__ = [
    "DAY", "WEEK", "FOUR_WEEKS", "OFF_NAME", "HOLIDAY_WEEKLY", "HOLIDAY_4W4D", "ShiftDef", "LawParams",
    "DomainParams", "GuidelineParams", "Rules", "Instance", "Eps", "inclusion_threshold", "night_band",
    "night_capable", "is_part", "weekend_days", "weekend_pairs", "Assignment", "full_sequences",
    "staff_on_duty", "night_units", "rest_cliques", "overnight_shifts", "holiday_windows", "cover_intervals",
    "symmetry_classes", "precheck", "Renderer", "Nurse", "Day", "DaySpan", "Span", "Cat", "Shift", "Clock", "Param",
    "CAT_NAMES", "WEEKDAYS", "ConstraintSpec", "Item", "Aux", "Named", "REGISTRY", "SCOPES", "MANDATORY", "TOGGLEABLE",
    "constraint", "selected_constraints", "toggleable", "NurseModel", "SOLVE_SOLVER", "SolveResult", "solve", "roster",
    "MUS_SOLVER", "Diagnosis", "FEASIBLE", "TIMEOUT", "CONFLICT", "diagnose", "explain",
    "SIZES", "hm", "synthetic", "synthetic_for", "with_requests",
]
