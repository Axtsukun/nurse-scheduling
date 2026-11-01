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

    def lv(m, path=()):
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


def test_module_dependencies_have_no_cycles():
    deps, _ = _actual_deps()
    assert all(d in deps for ds in deps.values() for d in ds)          # 依存先はすべて nurse 内のモジュール
    _levels(deps)                                                       # 循環があれば ValueError


def test_readme_dependency_table_matches_the_imports():
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


def test_readme_dependency_diagram_is_the_transitive_reduction_of_the_imports():
    deps, _ = _actual_deps()

    def reach(a, seen=None):
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


def _instance_attrs(cls) -> set[str]:
    out = set()
    for c in cls.__mro__:
        try:
            out |= set(re.findall(r"self\.(\w+)", inspect.getsource(c)))
        except (OSError, TypeError):
            pass
    return out


def _return_type(obj) -> str:
    fn = obj.fget if isinstance(obj, property) else obj
    try:
        ann = inspect.signature(fn).return_annotation
    except (TypeError, ValueError):
        return ""
    if ann is inspect.Signature.empty:
        return ""
    return ann if isinstance(ann, str) else getattr(ann, "__name__", str(ann))


def _diagram_classes():
    for fi, fig in enumerate(re.findall(r"```mermaid\n(.*?)```", CLASS_DOC, re.S), 1):
        for m in re.finditer(r"^( *)class (\w+)(?:\[[^\]]*\])? \{\n(.*?)\n\1\}", fig, re.S | re.M):
            yield fi, m.group(2), [ln.strip() for ln in m.group(3).split("\n") if ln.strip() and not ln.strip().startswith("<<")]


def test_class_diagram_matches_the_code():
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
def test_pages_site_builds_without_warnings(tmp_path):
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


def test_api_reference_covers_every_module():
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
