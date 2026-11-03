"""サンプル Web アプリのサーバー（webapp/server.py）: 入力 CSV の読み書き・実行・履歴・不正なリクエストの拒否。
フロント（JavaScript）は、サーバーの API を通してだけエンジンと CSV でやり取りする。"""
import json
import sys, pathlib
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from typing import Any
import pytest
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webapp"))
import server


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    srv: ThreadingHTTPServer = server.serve(tmp_path_factory.mktemp("ws"), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def call(base: str, method: str, path: str, body: Any = None) -> tuple[int, Any]:
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if r.headers["Content-Type"].startswith("application/json") else raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_static_page_and_unknown_paths(base: str) -> None:
    st, page = call(base, "GET", "/")
    assert st == 200 and "勤務表エンジン" in page.decode()
    assert call(base, "GET", "/../server.py")[0] == 404
    assert call(base, "GET", "/api/input/secret.csv")[0] == 404
    assert call(base, "GET", "/api/nothing")[0] == 404


def test_input_csv_round_trip(base: str) -> None:
    st, t = call(base, "GET", "/api/input/nurses.csv")
    assert st == 200 and t["rows"][0][0] == "氏名" and len(t["rows"]) == 31
    rows = t["rows"]
    rows[1][0] = "山田,太郎"                                       # カンマ・日本語も壊れない
    assert call(base, "PUT", "/api/input/nurses.csv", {"rows": rows})[0] == 200
    assert call(base, "GET", "/api/input/nurses.csv")[1]["rows"][1][0] == "山田,太郎"
    assert call(base, "PUT", "/api/input/nurses.csv", {"rows": "x"})[0] == 400
    assert call(base, "PUT", "/api/input/other.csv", {"rows": [["a"]]})[0] == 404
    assert call(base, "POST", "/api/init", {"nurses": 30, "days": 30})[0] == 200          # 見本に戻す
    assert call(base, "GET", "/api/input/nurses.csv")[1]["rows"][1][0] == "看護師01"
    assert call(base, "POST", "/api/init", {"nurses": 2})[0] == 400


def wait_done(base: str, run_id: str) -> dict[str, Any]:
    for _ in range(300):
        info = call(base, "GET", f"/api/runs/{run_id}")[1]
        if info["state"] == "done":
            return info
        time.sleep(0.5)
    raise AssertionError("実行が終わらない")


def test_run_keeps_input_and_output_and_allows_review(base: str) -> None:
    st, r = call(base, "POST", "/api/run")
    assert st == 202
    assert call(base, "POST", "/api/run")[0] == 409                 # 実行中は同時に実行できない
    info = wait_done(base, r["id"])
    assert info["returncode"] == 0 and info["status"] == "feasible" and info["n_nurses"] == "30"
    sched = call(base, "GET", f"/api/runs/{r['id']}/output/schedule.csv")[1]["rows"]
    assert len(sched) == 31
    # 実行後に入力を変えても、その実行の入力は残る（後から結果を見直せる）
    t = call(base, "GET", "/api/input/params.csv")[1]["rows"]
    t[2][1] = "5"
    call(base, "PUT", "/api/input/params.csv", {"rows": t})
    assert call(base, "GET", f"/api/runs/{r['id']}/input/params.csv")[1]["rows"][2][1] == "30"
    assert [x["id"] for x in call(base, "GET", "/api/runs")[1]][0] == r["id"]
    assert call(base, "GET", f"/api/runs/{r['id']}/log")[1]["text"]


def test_input_error_is_shown_as_a_result(base: str) -> None:
    call(base, "POST", "/api/init", {"nurses": 30, "days": 30})
    t = call(base, "GET", "/api/input/params.csv")[1]["rows"]
    t[2][1] = "0"                                                   # n_days=0
    call(base, "PUT", "/api/input/params.csv", {"rows": t})
    rid = call(base, "POST", "/api/run")[1]["id"]
    info = wait_done(base, rid)
    assert info["returncode"] == 30 and info["status"] == "error"
    st = call(base, "GET", f"/api/runs/{rid}/output/status.csv")[1]["rows"]
    assert any(r[0] == "message" and "n_days" in r[1] for r in st)
