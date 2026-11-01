"""griffe 拡張: クラス変数（dataclass の項目・NamedTuple の項目・Enum の値）の行末コメントを、その項目の説明として扱う。

このプロジェクトは項目の説明を `x: int   # 説明` の行末コメントで書いている。mkdocstrings は
文字列リテラルの docstring しか読まないため、そのままでは API 文書に項目の説明が出ない。
ソースを書き換えずに済むよう、ビルド時にコメントを読み取って docstring として付ける。
すでに docstring がある項目は上書きしない。
"""
from __future__ import annotations

import io
import tokenize
from functools import lru_cache

import griffe


@lru_cache(maxsize=None)
def _inline_comments(path: str) -> dict[int, str]:
    """{行番号: その行の行末コメント（先頭の # と空白を除く）}。コード付きのコメントだけを対象にする。"""
    out: dict[int, str] = {}
    with open(path, encoding="utf-8") as f:
        src = f.read()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT and tok.line[: tok.start[1]].strip():
            out[tok.start[0]] = tok.string.lstrip("#").strip()
    return out


class InlineComments(griffe.Extension):
    def on_class_members(self, *, node, cls: griffe.Class, agent, **kwargs) -> None:
        if cls.filepath is None:
            return
        comments = _inline_comments(str(cls.filepath))
        for member in cls.members.values():
            if member.is_alias or member.kind.value != "attribute" or member.docstring:
                continue
            text = comments.get(member.lineno or -1)
            if text:
                member.docstring = griffe.Docstring(text, parent=member)
