"""ドキュメントがコードと一致することの検査（ソルバー不要）。
  - README 3.4 の依存図・依存表が、nurse/ の実際の import と一致し、依存に循環がない
  - docs/design/class-diagram.md のクラス図が、コードのクラス・フィールド・操作・戻り値・列挙値と一致する
手書きの図・表は、コードの変更でずれやすいので、ずれたらここで気づけるようにする。"""
import ast
import dataclasses
import enum
import importlib
import inspect
import re
import sys, pathlib
from collections.abc import Iterator
from typing import Any
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import nurse_csp, verify, evaluate

PKG = ROOT / "nurse"
README = (ROOT / "README.md").read_text(encoding="utf-8")
CLASS_DOC = (ROOT / "docs/design/class-diagram.md").read_text(encoding="utf-8")


# ---- モジュール依存 ----
def _actual_deps() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """({モジュール: nurse 内の直接の依存}, {モジュール: 外部（標準ライブラリ以外）のライブラリ})"""
    deps, ext = {}, {}
    for p in sorted(PKG.glob("*.py")):
        if p.stem == "__init__":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        deps[p.stem] = {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 1 and n.module}
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module}
        ext[p.stem] = {x for x in names if x not in sys.stdlib_module_names and x != "nurse"}
    return deps, ext


def _levels(deps: dict[str, set[str]]) -> dict[str, int]:
    """段 = 依存先をたどった最長の長さ（依存のないモジュールが 0）。循環があれば ValueError"""
    out: dict[str, int] = {}

    def lv(m: str, path: tuple[str, ...] = ()) -> int:
        if m in path:
            raise ValueError(f"循環: {' → '.join(path + (m,))}")
        if m not in out:
            out[m] = 0 if not deps[m] else 1 + max(lv(d, path + (m,)) for d in deps[m])
        return out[m]
    for m in deps:
        lv(m)
    return out


def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end)]


def test_module_dependencies_have_no_cycles() -> None:
    deps, _ = _actual_deps()
    assert all(d in deps for ds in deps.values() for d in ds)          # 依存先はすべて nurse 内のモジュール
    _levels(deps)                                                       # 循環があれば ValueError


def test_readme_dependency_table_matches_the_imports() -> None:
    deps, ext = _actual_deps()
    level = _levels(deps)
    rows = {}
    for line in _section(README, "### 3.4", "### 3.5").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 5 and cells[0].isdigit():
            rows[cells[1].strip("`")] = (int(cells[0]), set(re.findall(r"`(\w+)`", cells[3])),
                                         set() if cells[4] == "—" else {x.strip() for x in cells[4].split(",")})
    assert set(rows) == set(deps), f"表にないモジュール/余分: {set(deps) ^ set(rows)}"
    for m, (lv, ds, es) in rows.items():
        assert (lv, ds, es) == (level[m], deps[m], ext[m]), f"{m}: 表={ (lv, sorted(ds), sorted(es)) } コード={ (level[m], sorted(deps[m]), sorted(ext[m])) }"


def test_readme_dependency_diagram_is_the_transitive_reduction_of_the_imports() -> None:
    deps, _ = _actual_deps()

    def reach(a: str, seen: set[str] | None = None) -> set[str]:
        seen = set() if seen is None else seen
        for b in deps[a]:
            if b not in seen:
                seen.add(b)
                reach(b, seen)
        return seen
    expected = {(a, b) for a in deps for b in deps[a] if not any(b in reach(c) for c in deps[a] - {b})}
    fig = re.search(r"```mermaid\n(flowchart.*?)```", _section(README, "### 3.4", "### 3.5"), re.S).group(1)
    drawn = {(a, b) for a, b in re.findall(r"^\s*(\w+) -\.-> (\w+)\s*$", fig, re.M) if a in deps and b in deps}
    assert drawn == expected, f"図のみ={sorted(drawn - expected)} コードのみ={sorted(expected - drawn)}"


# ---- クラス図 ----
_EXTERNAL = {"CPSAT", "Exact", "ValueError", "RuntimeError", "Assignment"}          # コード側に対応がない・別扱いのもの


def _code_objects() -> dict[str, object]:
    objs: dict[str, object] = {"nurse_csp": nurse_csp, "verify": verify, "evaluate": evaluate}
    for p in PKG.glob("*.py"):
        if p.stem == "__init__":
            continue
        mod = importlib.import_module(f"nurse.{p.stem}")
        objs[p.stem] = mod
        objs.update({k: v for k, v in vars(mod).items() if inspect.isclass(v) and v.__module__ == mod.__name__})
    return objs


def _generics(text: str) -> str:
    """Mermaid の総称型 list~str~ → list[str]（入れ子も）"""
    while (new := re.sub(r"(\w+)~([^~]+)~", r"\1[\2]", text)) != text:
        text = new
    return text


def _instance_attrs(cls: type) -> set[str]:
    out = set()
    for c in cls.__mro__:
        try:
            out |= set(re.findall(r"self\.(\w+)", inspect.getsource(c)))
        except (OSError, TypeError):
            pass
    return out


def _return_type(obj: Any) -> str:
    fn = obj.fget if isinstance(obj, property) else obj
    try:
        ann = inspect.signature(fn).return_annotation
    except (TypeError, ValueError):
        return ""
    if ann is inspect.Signature.empty:
        return ""
    return ann if isinstance(ann, str) else getattr(ann, "__name__", str(ann))


def _diagram_classes() -> Iterator[tuple[int, str, list[str]]]:
    for fi, fig in enumerate(re.findall(r"```mermaid\n(.*?)```", CLASS_DOC, re.S), 1):
        for m in re.finditer(r"^( *)class (\w+)(?:\[[^\]]*\])? \{\n(.*?)\n\1\}", fig, re.S | re.M):
            yield fi, m.group(2), [ln.strip() for ln in m.group(3).split("\n") if ln.strip() and not ln.strip().startswith("<<")]


def test_class_diagram_matches_the_code() -> None:
    objs = _code_objects()
    problems, checked = [], 0
    for fi, name, members in _diagram_classes():
        if name in _EXTERNAL:
            continue
        target = objs.get(name)
        if target is None:
            problems.append(f"図{fi}: {name} がコードにない")
            continue
        checked += 1
        is_class = inspect.isclass(target)
        attrs = [re.match(r"[+-](\w+)\s", x).group(1) for x in members if re.match(r"[+-]\w+\s", x) and "(" not in x]
        for x in members:
            if m := re.match(r"[+-](\w+)\((.*)\)\s*(.*)$", x):
                n, ret = m.group(1), _generics(m.group(3)).strip()
                if not hasattr(target, n):
                    problems.append(f"図{fi}: {name} に操作 {n} がない")
                    continue
                code_ret = _return_type(getattr(target, n))
                if ret and code_ret:                                    # 図に戻り値があり、コードに注釈があるとき比べる
                    same = ret == code_ret if "[" in ret else ret == code_ret.split("[")[0]
                    if not same:
                        problems.append(f"図{fi}: {name}.{n} の戻り値 図={ret} コード={code_ret}")
        for n in attrs:
            if not (hasattr(target, n) or (is_class and (n in getattr(target, "__annotations__", {}) or n in _instance_attrs(target)))):
                problems.append(f"図{fi}: {name} に属性 {n} がない")
        if is_class and dataclasses.is_dataclass(target) and attrs:
            fields = {f.name for f in dataclasses.fields(target)}
            if set(attrs) != fields:
                problems.append(f"図{fi}: {name} のフィールド不一致 図のみ={sorted(set(attrs) - fields)} コードのみ={sorted(fields - set(attrs))}")
        if is_class and issubclass(target, enum.Enum):                  # 列挙値（名前と値）
            listed = {m.group(1): int(m.group(2)) for x in members if (m := re.fullmatch(r"([A-Z_]+) = (\d+)", x))}
            actual = {e.name: int(e.value) for e in target}
            if listed and listed != actual:                              # 値を載せていない図（ステレオタイプのみ）は対象外
                problems.append(f"図{fi}: {name} の列挙値 図={listed} コード={actual}")
    assert checked > 30, f"照合できたクラス・モジュールが少なすぎる（{checked}）"
    assert not problems, "\n" + "\n".join(problems)


# ---- ドキュメントサイト（MkDocs。公開はしない） ----
def test_pages_site_builds_without_warnings(tmp_path: pathlib.Path) -> None:
    """mkdocs.yml のとおりにサイトが警告なしでビルドでき、README が index になり、作業メモが載らない。
    （MkDocs が無い環境ではスキップ。CI のワークフローも同じ --strict ビルドを行う）"""
    import subprocess
    pytest = importlib.import_module("pytest")
    pytest.importorskip("mkdocs")
    pytest.importorskip("material")
    site = tmp_path / "site"
    r = subprocess.run([sys.executable, "-m", "mkdocs", "build", "--strict", "-d", str(site)],
                       cwd=ROOT, capture_output=True, text=True, env={**__import__("os").environ, "NO_MKDOCS_2_WARNING": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ナーススケジューリング CSP エンジン" in (site / "index.html").read_text(encoding="utf-8")
    assert not (site / "notes").exists()
    # 掲載対象の文書にある Mermaid の図が、すべて mermaid ブロックとして出力される
    sources = [ROOT / "README.md", *(p for p in (ROOT / "docs").rglob("*.md") if "notes" not in p.relative_to(ROOT / "docs").parts)]
    expected = sum(p.read_text(encoding="utf-8").count("```mermaid") for p in sources)
    n = sum(p.read_text(encoding="utf-8").count('class="mermaid"') for p in site.rglob("index.html"))
    assert n == expected > 0, (n, expected)


def test_api_reference_covers_every_module() -> None:
    """API リファレンス（docs/api/）が、全モジュールを漏れなく載せ、mkdocs.yml の nav にも入っていること。
    モジュールを足したのにページを足し忘れると、ここで気づく。"""
    modules = ["nurse_csp", "verify", "evaluate", "nurse"] + [f"nurse.{p.stem}" for p in PKG.glob("*.py") if p.stem != "__init__"]
    nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    for m in modules:
        page = ROOT / "docs/api" / f"{m}.md"
        assert page.exists(), f"{m}: docs/api/{m}.md がない"
        assert f"::: {m}\n" in page.read_text(encoding="utf-8"), f"{m}: ページに ::: {m} がない"
        assert f"api/{m}.md" in nav, f"{m}: mkdocs.yml の nav にない"
    extra = {p.stem for p in (ROOT / "docs/api").glob("*.md")} - set(modules) - {"index"}
    assert not extra, f"存在しないモジュールのページ: {extra}"


# ---- API 文書の読みやすさ（docstring） ----
_API_FILES = [ROOT / f for f in ("nurse_csp.py", "verify.py", "evaluate.py")] + sorted(PKG.glob("*.py"))


def _functions(body: list[ast.stmt], prefix: str = "") -> Iterator[tuple[str, ast.FunctionDef]]:
    """モジュール・クラス直下の関数・メソッドと、その名前（入れ子の関数は含まない）。"""
    for n in body:
        if isinstance(n, ast.FunctionDef):
            yield prefix + n.name, n
        elif isinstance(n, ast.ClassDef):
            yield from _functions(n.body, prefix + n.name + ".")


def _public_docstring_owners() -> Iterator[tuple[pathlib.Path, str, ast.Module | ast.FunctionDef | ast.ClassDef]]:
    """(ファイル, 名前, ノード): モジュール・公開のクラス・公開の関数/メソッド（_ で始まるものは除く）"""
    for p in _API_FILES:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        yield p, "(module)", tree

        def classes_and_functions(body: list[ast.stmt], prefix: str) -> Iterator[tuple[pathlib.Path, str, ast.Module | ast.FunctionDef | ast.ClassDef]]:
            for n in body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not n.name.startswith("_"):
                    yield p, prefix + n.name, n
                    if isinstance(n, ast.ClassDef):
                        yield from classes_and_functions(n.body, prefix + n.name + ".")
        yield from classes_and_functions(tree.body, "")


def test_every_public_api_has_a_docstring() -> None:
    """API リファレンスは docstring から作る。公開のモジュール・クラス・関数に説明が無いと、文書に何も出ない。"""
    missing = [f"{p.name}:{name}" for p, name, n in _public_docstring_owners() if not ast.get_docstring(n)]
    assert not missing, "docstring が無い: " + ", ".join(missing)


def test_docstring_lists_and_tables_have_a_blank_line_before() -> None:
    """docstring は Markdown として表示される。箇条書き・番号リスト・表・見出し・コードの前に空行が無いと、
    直前の段落に連結して1行に潰れる（読めなくなる）。"""
    item = re.compile(r"^\s*([-*] |\d+\. |#{1,6} |\||```)")
    bad = []
    for p, name, n in _public_docstring_owners():
        doc = ast.get_docstring(n)
        if not doc:
            continue
        lines, fence = doc.split("\n"), False
        for i, line in enumerate(lines):
            if line.strip().startswith("```"):
                if not fence and i and lines[i - 1].strip():
                    bad.append(f"{p.name}:{name} 行{i + 1}: {line.strip()[:30]}")
                fence = not fence
                continue
            if fence or not item.match(line) or i == 0:
                continue
            prev = lines[i - 1]
            if prev.strip() and not item.match(prev) and not re.match(r"^\s+\S", prev):
                bad.append(f"{p.name}:{name} 行{i + 1}: {line.strip()[:30]}")
    assert not bad, "リスト・表・見出しの前に空行が無い:\n" + "\n".join(bad)


def test_every_function_documents_its_args_and_returns() -> None:
    """公開・内部を問わず、引数がある関数は `Args:`、値を返す関数（戻り値の型注釈が None 以外）は `Returns:` の節を持つ。
    API リファレンスの引数表・戻り値表の元になる。property は対象外（名前と1行の説明で足りる）。"""
    missing = []
    for p in _API_FILES:
        for name, n in _functions(ast.parse(p.read_text(encoding="utf-8")).body):
            if any(isinstance(d, ast.Name) and d.id == "property" for d in n.decorator_list):
                continue
            doc = ast.get_docstring(n) or ""
            params = [a.arg for a in n.args.args + n.args.kwonlyargs if a.arg not in ("self", "cls")]
            returns = n.returns is not None and ast.unparse(n.returns) != "None"
            if not doc or (params and "Args:" not in doc) or (returns and "Returns:" not in doc):
                missing.append(f"{p.name}:{name}")
    assert not missing, "Args / Returns の節が足りない: " + ", ".join(missing)


# ---- 型ヒント ----
_TYPED_FILES = _API_FILES + sorted((ROOT / "tools").glob("*.py")) + sorted((ROOT / "tests").glob("*.py"))


def test_every_function_has_type_hints() -> None:
    """型ヒントは必須: 製品コード・ツール・テストの全関数（入れ子の関数も）で、self / cls 以外のすべての引数と、戻り値に型を付ける。
    （lambda は書けないので対象外。`*args` / `**kwargs` も型を付ける）"""
    missing = []
    for p in _TYPED_FILES:
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            a = n.args
            params = [x for x in a.posonlyargs + a.args + a.kwonlyargs if x.arg not in ("self", "cls")]
            params += [v for v in (a.vararg, a.kwarg) if v is not None]
            lacking = [x.arg for x in params if x.annotation is None] + (["戻り値"] if n.returns is None else [])
            if lacking:
                missing.append(f"{p.relative_to(ROOT)}:{n.lineno} {n.name}（{', '.join(lacking)}）")
    assert not missing, "型ヒントが無い:\n" + "\n".join(missing)
