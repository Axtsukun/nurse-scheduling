"""テスト共通の設定。ログの出力先をテストごとの一時ディレクトリにし、テスト後にログのハンドラを外す。"""
import pathlib
from collections.abc import Iterator

import pytest

from nurse import reset_logging


@pytest.fixture(autouse=True)
def _isolated_logging(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """CLI のテストが、リポジトリ直下に `logs/` を作らないようにする。

    Args:
        tmp_path: テストごとの一時ディレクトリ。
        monkeypatch: 環境変数の差し替え用。

    Yields:
        テストの実行中（終了後に、ログのハンドラを外す）。"""
    config = tmp_path / "test.logging.yaml"
    config.write_text(f"log_dir: {tmp_path / 'logs'}\n", encoding="utf-8")
    monkeypatch.setenv("NURSE_LOGGING", str(config))
    yield
    reset_logging()
