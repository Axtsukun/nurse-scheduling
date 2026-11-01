"""MkDocs フック: リポジトリ直下の README.md を、サイトのトップ（index.md）として組み込む。

README は GitHub 上の表紙でもあるため docs/ の外にある。複製を置くと内容がずれるので、
ビルド時にだけ仮想ファイルとして追加する。
"""
from pathlib import Path

from mkdocs.structure.files import File

_README = Path(__file__).resolve().parent.parent / "README.md"


def on_files(files, config):
    files.append(File.generated(config, "index.md", abs_src_path=str(_README)))
    return files
