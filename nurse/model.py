"""制約モデル NurseModel: カーネル + 層1（労働法規）+ 層2（病院共通）+ 層3（任意）の制約を持つ。"""
from __future__ import annotations
from .law import LawConstraints
from .domain import DomainConstraints
from .optional import OptionalConstraints


class NurseModel(LawConstraints, DomainConstraints, OptionalConstraints):
    """各層のミックスイン（いずれも ModelKernel を継承）を合成したモデル。
    制約を足すときは、該当する層のモジュールにメソッドを書いて `@constraint` で登録する（CLAUDE.md 参照）。"""
