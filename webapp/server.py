"""
サンプル Web アプリのサーバー（標準ライブラリのみ。ローカルでの動作確認用）。

フロントとエンジンの間の IF は CSV ファイルだけ。このサーバーは、
- 入力 CSV（`workspace/input/`）の読み書き（フロントの編集内容を CSV にする）
- エンジン（`nurse_csv.py run`）の別プロセスでの起動
- 実行ごとの入力・出力 CSV の保管（`workspace/runs/<実行ID>/{input,output}/`）。過去の結果はいつでも見直せる
を仲介する。エンジンの Python コードは import しない（CSV と終了コードで受け渡す）。

起動:  python webapp/server.py `[--port 8000] [--workspace 作業ディレクトリ]`  →  http://127.0.0.1:8000/
"""
from __future__ import annotations
import argparse
import csv
import io
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = HERE / "static"
ENGINE = ROOT / "nurse_csv.py"
INPUT_FILES = ("shifts.csv", "nurses.csv", "history.csv", "demand.csv", "fixed.csv", "requests.csv", "params.csv", "constraints.csv")
OUTPUT_FILES = ("status.csv", "schedule.csv", "conflicts.csv", "precheck.csv")
RUN_ID = re.compile(r"^\d{8}-\d{6}(-\d+)?$")
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


class Workspace:
    """作業ディレクトリ（現在の入力 CSV と、実行ごとの保管）と、エンジンの起動を扱う"""

    def __init__(self, root: Path) -> None:
        """作業ディレクトリを決める（無ければ作る）。

        Args:
            root: 作業ディレクトリ。"""
        self.root = root
        self.input = root / "input"
        self.runs = root / "runs"
        self.lock = threading.Lock()
        self.running: str | None = None            # 実行中の実行ID（同時に1つだけ）
        self.runs.mkdir(parents=True, exist_ok=True)

    def init_input(self, nurses: int = 30, days: int = 30, seed: int = 0) -> None:
        """入力 CSV を見本で作り直す（エンジンの `init`）。

        Args:
            nurses: 看護師数。
            days: 計画日数。
            seed: 乱数の種。"""
        tmp = self.root / "input.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        r = subprocess.run([sys.executable, str(ENGINE), "init", str(tmp), "--nurses", str(nurses), "--days", str(days), "--seed", str(seed)],
                           capture_output=True, text=True, cwd=ROOT)
        if r.returncode != 0:
            raise ValueError((r.stderr or r.stdout).strip())
        shutil.rmtree(self.input, ignore_errors=True)
        tmp.rename(self.input)

    def run_ids(self) -> list[str]:
        """実行ID（新しい順）

        Returns:
            実行IDのリスト。"""
        return sorted((p.name for p in self.runs.iterdir() if p.is_dir() and RUN_ID.match(p.name)), reverse=True)

    def start_run(self) -> str:
        """現在の入力を保管して、エンジンを別プロセスで起動する。実行中なら `RuntimeError`。

        Returns:
            実行ID。"""
        with self.lock:
            if self.running:
                raise RuntimeError(f"実行中です（{self.running}）。終わるまで待ってください")
            run_id = time.strftime("%Y%m%d-%H%M%S")
            n = 0
            while (self.runs / run_id).exists():
                n += 1
                run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{n}"
            d = self.runs / run_id
            shutil.copytree(self.input, d / "input")
            (d / "output").mkdir()
            self.running = run_id
        threading.Thread(target=self._execute, args=(run_id,), daemon=True).start()
        return run_id

    def _execute(self, run_id: str) -> None:
        """エンジンを実行して終了を待つ（別スレッド）。標準出力・標準エラーは `engine.log` に残す

        Args:
            run_id: 実行ID。"""
        d = self.runs / run_id
        t0 = time.time()
        try:
            with (d / "engine.log").open("w", encoding="utf-8") as log:
                p = subprocess.run([sys.executable, str(ENGINE), "run", str(d / "input"), str(d / "output")], stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
            meta: dict[str, Any] = {"returncode": p.returncode, "seconds": round(time.time() - t0, 1)}
        except Exception as e:                                             # 起動できないなど
            meta = {"returncode": -1, "seconds": round(time.time() - t0, 1), "error": str(e)}
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        with self.lock:
            self.running = None

    def run_info(self, run_id: str) -> dict[str, Any]:
        """実行1件の概要（状態と、状態・開始日などの要約）

        Args:
            run_id: 実行ID。

        Returns:
            `{id, state, returncode, status, label, seconds}`。state は running / done。"""
        d = self.runs / run_id
        meta = d / "meta.json"
        info: dict[str, Any] = {"id": run_id, "state": "done" if meta.exists() else "running"}
        if meta.exists():
            info.update(json.loads(meta.read_text(encoding="utf-8")))
        status = {r[0]: r[1] for r in read_csv(d / "output" / "status.csv")[1:] if len(r) > 1}
        info.update(status=status.get("status", ""), label=status.get("label", ""), n_nurses=status.get("n_nurses", ""), n_days=status.get("n_days", ""))
        return info


def read_csv(path: Path) -> list[list[str]]:
    """CSV を行のリストで読む（無ければ空）

    Args:
        path: CSV ファイル。

    Returns:
        行のリスト（先頭が見出し）。"""
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.reader(f)]


def write_csv(path: Path, rows: list[list[str]]) -> None:
    """CSV を書く（BOM 付き UTF-8。Excel でそのまま開ける）

    Args:
        path: 書き込み先。
        rows: 行のリスト（先頭が見出し）。"""
    buf = io.StringIO(newline="")
    csv.writer(buf).writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")


def make_handler(ws: Workspace) -> type[BaseHTTPRequestHandler]:
    """作業ディレクトリに結び付いた HTTP ハンドラのクラスを作る

    Args:
        ws: 作業ディレクトリ。

    Returns:
        `BaseHTTPRequestHandler` のサブクラス。"""

    class Handler(BaseHTTPRequestHandler):
        """API（`/api/...`）と静的ファイルを返す"""

        def log_message(self, format: str, *args: Any) -> None:
            """アクセスログは出さない"""

        def send_json(self, obj: Any, status: int = 200) -> None:
            """JSON を返す

            Args:
                obj: 返す値。
                status: HTTP ステータス。"""
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def error(self, status: int, message: str) -> None:
            """エラーを JSON で返す

            Args:
                status: HTTP ステータス。
                message: メッセージ。"""
            self.send_json({"error": message}, status)

        def body(self) -> Any:
            """リクエスト本文の JSON

            Returns:
                JSON を読んだ値（本文が空なら None）。"""
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else None

        def do_GET(self) -> None:
            """GET: 入力 CSV・実行一覧・実行結果・静的ファイル"""
            path = self.path.split("?")[0]
            m = re.fullmatch(r"/api/input/([\w.]+)", path)
            if m:
                return self.get_table(ws.input / m.group(1), m.group(1), INPUT_FILES)
            if path == "/api/runs":
                return self.send_json([ws.run_info(r) for r in ws.run_ids()])
            m = re.fullmatch(r"/api/runs/([\d-]+)", path)
            if m and RUN_ID.match(m.group(1)) and (ws.runs / m.group(1)).is_dir():
                return self.send_json(ws.run_info(m.group(1)))
            m = re.fullmatch(r"/api/runs/([\d-]+)/(input|output)/([\w.]+)", path)
            if m and RUN_ID.match(m.group(1)):
                return self.get_table(ws.runs / m.group(1) / m.group(2) / m.group(3), m.group(3), INPUT_FILES + OUTPUT_FILES)
            m = re.fullmatch(r"/api/runs/([\d-]+)/log", path)
            if m and RUN_ID.match(m.group(1)):
                f = ws.runs / m.group(1) / "engine.log"
                return self.send_json({"text": f.read_text(encoding="utf-8") if f.exists() else ""})
            if path.startswith("/api/"):
                return self.error(404, "見つかりません")
            name = "index.html" if path == "/" else path.lstrip("/")
            f = (STATIC / name).resolve()
            if STATIC not in f.parents or not f.is_file():
                return self.error(404, "見つかりません")
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(f.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def get_table(self, file: Path, name: str, allowed: tuple[str, ...]) -> None:
            """CSV を {name, rows} で返す（rows の先頭が見出し）

            Args:
                file: CSV ファイル。
                name: ファイル名（許可された名前だけ）。
                allowed: 許可するファイル名。"""
            if name not in allowed:
                return self.error(404, f"{name} は扱えません")
            self.send_json({"name": name, "rows": read_csv(file), "exists": file.exists()})

        def do_PUT(self) -> None:
            """PUT /api/input/<名前>: 入力 CSV を保存する（本文: {rows: [[...]]}）"""
            m = re.fullmatch(r"/api/input/([\w.]+)", self.path)
            if not m or m.group(1) not in INPUT_FILES:
                return self.error(404, "見つかりません")
            rows = (self.body() or {}).get("rows")
            if not isinstance(rows, list) or not rows or not all(isinstance(r, list) for r in rows):
                return self.error(400, "rows は見出しを含む行のリスト")
            write_csv(ws.input / m.group(1), [[str(c) for c in r] for r in rows])
            self.send_json({"saved": m.group(1)})

        def do_POST(self) -> None:
            """POST: /api/init（見本で作り直す）・/api/run（エンジンを実行する）"""
            try:
                if self.path == "/api/init":
                    b = self.body() or {}
                    ws.init_input(int(b.get("nurses", 30)), int(b.get("days", 30)), int(b.get("seed", 0)))
                    return self.send_json({"ok": True})
                if self.path == "/api/run":
                    return self.send_json({"id": ws.start_run()}, HTTPStatus.ACCEPTED)
            except RuntimeError as e:
                return self.error(409, str(e))
            except (ValueError, TypeError) as e:
                return self.error(400, str(e))
            self.error(404, "見つかりません")

    return Handler


def serve(workspace: Path, port: int = 8000, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """サーバーを作る（`serve_forever()` で起動する）。入力 CSV が無ければ見本を作る。

    Args:
        workspace: 作業ディレクトリ。
        port: ポート（0 なら空いているポート）。
        host: 待ち受けるアドレス（既定はローカルのみ）。

    Returns:
        HTTP サーバー。"""
    ws = Workspace(workspace)
    if not (ws.input / "params.csv").exists():
        ws.init_input()
    return ThreadingHTTPServer((host, port), make_handler(ws))


def main(argv: list[str] | None = None) -> int:
    """コマンドライン

    Args:
        argv: 引数（省略すると `sys.argv`）。

    Returns:
        終了コード。"""
    ap = argparse.ArgumentParser(description="サンプル Web アプリ（ローカル用）")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--workspace", type=Path, default=HERE / "workspace", help="入力 CSV と実行結果の保管場所")
    args = ap.parse_args(argv)
    srv = serve(args.workspace, args.port)
    print(f"http://127.0.0.1:{srv.server_address[1]}/ で起動しました（Ctrl+C で終了）。作業ディレクトリ: {args.workspace}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("終了します")
    return 0


if __name__ == "__main__":
    sys.exit(main())
