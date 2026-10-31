import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import nurse_csp as m
import verify, evaluate


def test_evaluate_consistent_with_solution():
    I, eps = m.synthetic(), m.Eps()
    ok, _, M, _, _ = m.solve(I, eps, time_limit=60)
    assert ok
    A = verify.extract(M)
    ev = evaluate.evaluate(I, A)
    assert ev["n_regular"] == 26 and ev["n_part"] == 4
    assert all(pres >= req for r in ev["coverage"] for _, _, req, pres in r["bands"])
    # 区分別割当の合計 = 勤務の延べ数 = 人ごとの勤務回数の総和
    tot = sum(sum(r["by_cat"].values()) for r in ev["coverage"])
    assert tot == sum(ev[k]["勤務回数"]["mean"] * ev["n_" + k] for k in ("regular", "part"))
    assert ev["night72"]["avg_included"] <= I.limit_units / 2
    assert abs(ev["night72"]["avg_included"] - verify.report(I, A)["avg_night_hours"]) < 1e-9


def test_weekend_stats_matches_verify_definition():
    I, eps = m.synthetic(), m.Eps()
    ok, _, M, _, _ = m.solve(I, eps, time_limit=60)
    assert ok
    A = verify.extract(M)
    wk = evaluate.weekend_stats(I, A)
    assert all(r["weekends"] <= I.max_work_weekends for r in wk["rows"])
    assert all(r["days"] == r["sat"] + r["sun"] for r in wk["rows"])
    assert wk["n_sat"] == 4 and wk["n_sun"] == 4


def test_weekend_spread_within_eps():
    I, eps = m.synthetic(), m.Eps()
    ok, _, M, _, _ = m.solve(I, eps, time_limit=60)
    assert ok
    g = evaluate.weekend_stats(I, verify.extract(M))["groups"]
    assert g["regular"]["days"]["max"] - g["regular"]["days"]["min"] <= eps.weekend_spread_regular
    assert g["part"]["days"]["max"] - g["part"]["days"]["min"] <= eps.weekend_spread_part
