"""MUS プロセス（nurse/mus_worker.py）と、診断からの呼び出し（nurse/diagnosis.py の `_mus_in_worker`）。
やりとりするメッセージは nurse/mus_protocol.py。診断を呼ぶプロセスが exact を読み込まないことも確かめる。"""
import os
import pathlib
import pickle
import subprocess
import sys
import time
import cpmpy as cp
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import nurse_csp as m
from nurse import diagnosis, mus_protocol as proto, mus_worker

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _model() -> tuple[list, list]:
    """soft = [a == 1, a == 0, b == 1]。MUS は先頭の 2 つ（添字 0 と 1）。b == 1 は無関係"""
    a, b = cp.intvar(0, 1, name="a"), cp.intvar(0, 1, name="b")
    return [], [a == 1, a == 0, b == 1]


def _run_in_worker_dir(tmp_path: pathlib.Path, request: dict, *flags: str, env: dict[str, str] | None = None) -> dict:
    """request を書いて MUS プロセスを起動し、response を返す

    Args:
        tmp_path: 作業ディレクトリ。
        request: request の辞書。
        flags: Python に渡すオプション（`-O` など）。
        env: 子プロセスの環境変数（省略すると現在の環境に、PYTHONPATH だけ足す）。

    Returns:
        response の辞書。"""
    proto.write_message(tmp_path / proto.REQUEST_FILE, request)
    e = dict(os.environ if env is None else env, PYTHONPATH=str(ROOT))
    subprocess.run([sys.executable, *flags, "-m", "nurse.mus_worker", str(tmp_path)], env=e, check=True, capture_output=True)
    return proto.read_message(tmp_path / proto.RESPONSE_FILE, proto.RESPONSE_KEYS, "response")


# ---- メッセージ（nurse/mus_protocol.py） ----
def test_request_keeps_variables_shared_between_hard_and_soft(tmp_path: pathlib.Path) -> None:
    """hard と soft に共通する変数が、読み戻しても同じオブジェクトのまま（別々に pickle すると複製されて意味が壊れる）"""
    x = cp.intvar(0, 5, name="x")
    proto.write_message(tmp_path / "r.pkl", proto.make_request([x >= 1], [x <= 0], "exact", 3.0))
    req = proto.read_message(tmp_path / "r.pkl", proto.REQUEST_KEYS, "request")
    (hv,), (sv,) = cp.transformations.get_variables.get_variables(req["hard"][0]), cp.transformations.get_variables.get_variables(req["soft"][0])
    assert hv is sv


def test_malformed_messages_are_rejected_with_protocol_error(tmp_path: pathlib.Path) -> None:
    """壊れたファイル・辞書でないもの・キーがないもの・版が違うものは、ProtocolError（内容は書かない）"""
    p = tmp_path / "x.pkl"
    p.write_bytes(b"not a pickle")
    with pytest.raises(proto.ProtocolError, match="読めない"):
        proto.read_message(p, proto.REQUEST_KEYS, "request")
    p.write_bytes(pickle.dumps([1, 2]))
    with pytest.raises(proto.ProtocolError, match="辞書ではない"):
        proto.read_message(p, proto.REQUEST_KEYS, "request")
    p.write_bytes(pickle.dumps({"protocol_version": proto.PROTOCOL_VERSION}))
    with pytest.raises(proto.ProtocolError, match="キーがない"):
        proto.read_message(p, proto.REQUEST_KEYS, "request")
    bad = proto.make_core([1], 0.1)
    bad["protocol_version"] = proto.PROTOCOL_VERSION + 1
    p.write_bytes(pickle.dumps(bad))
    with pytest.raises(proto.ProtocolError, match="不一致"):
        proto.read_message(p, proto.CORE_KEYS, "core")


def test_write_message_leaves_no_temporary_file(tmp_path: pathlib.Path) -> None:
    """書き込みは一時ファイル経由（読む側が書きかけを読まない）。終わったら一時ファイルは残らない"""
    d = tmp_path / "w"
    d.mkdir()
    proto.write_message(d / "a.pkl", proto.make_core([2, 0], 0.5))
    assert sorted(f.name for f in d.iterdir()) == ["a.pkl"]
    assert proto.read_message(d / "a.pkl", proto.CORE_KEYS, "core")["core_indices"] == [0, 2]


# ---- MUS プロセスの中身（solve_request） ----
@pytest.mark.parametrize("solver", ["exact", "ortools"])
def test_worker_returns_the_mus_as_soft_indices_and_reports_the_precandidate_first(solver: str) -> None:
    """MUS を soft の添字で返し、MUS の前に事前候補（コア）を知らせる。無関係な制約（添字 2）は含まない"""
    hard, soft = _model()
    cores: list[list[int]] = []
    resp = mus_worker.solve_request(proto.make_request(hard, soft, solver, 10.0), lambda core, elapsed: cores.append(core))
    assert resp["status"] == proto.MUS and resp["mus_indices"] == [0, 1] and set(cores[0]) >= {0, 1}
    assert set(resp["timings"]) == {"core", "mus"}


def test_worker_reports_sat_when_the_model_is_satisfiable() -> None:
    a = cp.intvar(0, 1, name="a")
    resp = mus_worker.solve_request(proto.make_request([], [a == 1], "ortools", 10.0), lambda core, elapsed: None)
    assert resp["status"] == proto.SAT and resp["mus_indices"] == []


def test_worker_reports_an_empty_mus_when_the_hard_constraints_alone_are_unsatisfiable() -> None:
    a = cp.intvar(0, 1, name="a")
    resp = mus_worker.solve_request(proto.make_request([a == 1, a == 0], [a >= 0], "ortools", 10.0), lambda core, elapsed: None)
    assert resp["status"] == proto.MUS and resp["mus_indices"] == []


def test_worker_reports_an_unavailable_solver_with_the_install_hint() -> None:
    hard, soft = _model()
    resp = mus_worker.solve_request(proto.make_request(hard, soft, "no_such_solver", 10.0), lambda core, elapsed: None)
    assert resp["status"] == proto.UNAVAILABLE and "pip install exact" in resp["message"]


def test_worker_rejects_soft_items_that_cannot_be_mapped_back_to_indices() -> None:
    """式でない要素と、同じオブジェクトの重複は、添字に戻せないので ERROR（診断は、これが起きないようにしている）"""
    a = cp.intvar(0, 1, name="a")
    c = a == 1
    for soft in ([c, c], [c, True]):
        resp = mus_worker.solve_request(proto.make_request([], soft, "ortools", 10.0), lambda core, elapsed: None)
        assert resp["status"] == proto.ERROR and "不正" in resp["message"]


# ---- 別プロセスとしての実行 ----
def test_worker_process_writes_ready_core_and_response(tmp_path: pathlib.Path) -> None:
    hard, soft = _model()
    resp = _run_in_worker_dir(tmp_path, proto.make_request(hard, soft, "exact", 10.0))
    assert resp["status"] == proto.MUS and resp["mus_indices"] == [0, 1]
    assert (tmp_path / proto.READY_FILE).exists()
    assert proto.read_message(tmp_path / proto.CORE_FILE, proto.CORE_KEYS, "core")["core_indices"]


def test_worker_process_refuses_to_run_with_python_O(tmp_path: pathlib.Path) -> None:
    """`-O` では assert が無効になり、充足可能を検出できない。黙って誤った結果を返さず、ERROR にする"""
    hard, soft = _model()
    resp = _run_in_worker_dir(tmp_path, proto.make_request(hard, soft, "exact", 10.0), "-O")
    assert resp["status"] == proto.ERROR and "-O" in resp["message"]


def test_worker_process_writes_an_error_response_when_the_request_is_unreadable(tmp_path: pathlib.Path) -> None:
    (tmp_path / proto.REQUEST_FILE).write_bytes(b"garbage")
    subprocess.run([sys.executable, "-m", "nurse.mus_worker", str(tmp_path)], env=dict(os.environ, PYTHONPATH=str(ROOT)), check=True, capture_output=True)
    resp = proto.read_message(tmp_path / proto.RESPONSE_FILE, proto.RESPONSE_KEYS, "response")
    assert resp["status"] == proto.ERROR and "request" in resp["message"]


# ---- 診断からの呼び出し（_mus_in_worker） ----
def test_mus_in_worker_returns_a_minimal_mus() -> None:
    hard, soft = _model()
    got = diagnosis._mus_in_worker(soft, hard, "exact", 30.0)
    assert got is not None and got.indices == [0, 1] and got.minimal and got.startup > 0


def test_mus_in_worker_does_not_inherit_pythonoptimize(monkeypatch: pytest.MonkeyPatch) -> None:
    """親の環境に PYTHONOPTIMIZE があっても、MUS プロセスは `-O` で動かない（引き継がない）"""
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    hard, soft = _model()
    got = diagnosis._mus_in_worker(soft, hard, "exact", 30.0)
    assert got is not None and got.minimal


def test_mus_in_worker_raises_solver_unavailable_for_an_unknown_solver() -> None:
    hard, soft = _model()
    with pytest.raises(m.SolverUnavailableError, match="pip install exact"):
        diagnosis._mus_in_worker(soft, hard, "no_such_solver", 30.0)


def test_mus_in_worker_reports_an_internal_error_when_the_process_dies_without_a_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """結果を返さずに終了したら、内部エラー（黙って「原因不明」にしない）"""
    monkeypatch.setattr(sys, "executable", "/bin/true")
    hard, soft = _model()
    with pytest.raises(m.InternalError, match="結果を返さずに終了"):
        diagnosis._mus_in_worker(soft, hard, "exact", 30.0)


def test_mus_in_worker_raises_an_internal_error_when_the_model_is_satisfiable() -> None:
    a = cp.intvar(0, 1, name="a")
    with pytest.raises(m.InternalError, match="充足可能"):
        diagnosis._mus_in_worker([a == 1], [], "ortools", 30.0)


def test_wait_worker_kills_a_process_that_exceeds_the_time_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """起動の上限（ready が出ない）を超えたら止める。止めた後にプロセスが残らない"""
    monkeypatch.setattr(diagnosis, "STARTUP_LIMIT", 0.3)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    t0 = time.perf_counter()
    assert diagnosis._wait_worker(proc, tmp_path, time.perf_counter(), 1.0) is True
    assert time.perf_counter() - t0 < 5 and proc.poll() is not None


def test_wait_worker_applies_the_budget_after_ready_and_after_core(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ready が出たら事前候補の求解（budget + 猶予）、core が出たら MUS（budget）の時間で止める"""
    monkeypatch.setattr(diagnosis, "CORE_GRACE", 0.0)
    (tmp_path / proto.READY_FILE).touch()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    t0 = time.perf_counter()
    assert diagnosis._wait_worker(proc, tmp_path, time.perf_counter(), 0.5) is True
    assert 0.4 < time.perf_counter() - t0 < 5
    (tmp_path / proto.CORE_FILE).touch()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    t1 = time.perf_counter()
    assert diagnosis._wait_worker(proc, tmp_path, time.perf_counter(), 0.5) is True
    assert 0.4 < time.perf_counter() - t1 < 5


# ---- 診断を呼ぶプロセスは exact を読み込まない（ライセンスの境界・環境の分離の前提） ----
def test_diagnose_does_not_load_exact_in_the_calling_process() -> None:
    """exact は MUS プロセスの中だけで動く。診断を呼ぶプロセスの `sys.modules` に exact は入らない"""
    code = ("import sys, dataclasses as dc; import nurse_csp as m\n"
            "I = m.synthetic(); I = dc.replace(I, off_days=[I.n_days + 1] + I.off_days[1:])\n"
            "d = m.diagnose(I, m.Eps(), time_limit=60)\n"
            "print(d.status, d.mus_solver, 'exact' in sys.modules)\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert out[-3:] == ["conflict", "exact", "False"]
