"""層構成（カーネル・層1 労働法規・層2 病院共通・層3 任意）の整合と、層1の休日の窓。ソルバー不要。"""
import dataclasses as dc
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
import nurse_csp as m

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_registry_matches_legal_catalog() -> None:
    """登録した制約の source は constraints.yaml の id に実在し、yaml の model は登録名に実在する"""
    yaml = pytest.importorskip("yaml")
    cat = yaml.safe_load((ROOT / "docs/legal/constraints.yaml").read_text(encoding="utf-8"))
    ids = {c["id"] for c in cat}
    for nm, sp in m.REGISTRY.items():
        assert set(sp.source) <= ids, (nm, set(sp.source) - ids)
    for c in cat:
        if c["model"]:
            assert c["model"] in m.REGISTRY, c


def test_scopes_declared() -> None:
    assert {sp.scope for sp in m.REGISTRY.values()} <= set(m.SCOPES)
    assert m.REGISTRY["holiday"].scope == "law"
    assert m.REGISTRY["72h"].scope == "domain" and m.REGISTRY["rest"].scope == "optional"
    assert set(m.toggleable()).isdisjoint(n for n, sp in m.REGISTRY.items() if sp.scope in m.MANDATORY)


def test_mandatory_constraints_cannot_be_switched_off() -> None:
    I = m.synthetic()
    for nm in ("struct", "holiday"):
        with pytest.raises(ValueError):
            m.selected_constraints(dc.replace(I, rules=dc.replace(I.rules, off=frozenset({nm}))))
        with pytest.raises(ValueError):
            m.selected_constraints(I, enabled=set(m.REGISTRY) - {nm})
    J = dc.replace(I, rules=dc.replace(I.rules, off=frozenset({"72h", "rest"})))
    sel = m.selected_constraints(J)
    assert "72h" not in sel and "rest" not in sel and "holiday" in sel and "struct" in sel
    with pytest.raises(ValueError):
        m.selected_constraints(dc.replace(I, rules=dc.replace(I.rules, off=frozenset({"no_such"}))))


def test_inclusion_threshold_by_admission_class() -> None:
    I = m.synthetic()
    assert m.inclusion_threshold(I, 0) == 32                                  # 7対1: 16h 未満を除外
    J = dc.replace(I, rules=dc.replace(I.rules, domain=dc.replace(I.rules.domain, admission_class="other")),
                   short_time=[1], incl_threshold={2: 10})
    assert m.inclusion_threshold(J, 0) == 16                                  # その他: 8h 未満を除外
    assert m.inclusion_threshold(J, 1) == 24                                  # 短時間正職員: 12h
    assert m.inclusion_threshold(J, 2) == 10                                  # 個別上書きが最優先


@pytest.mark.parametrize("start_weekday", range(7))
@pytest.mark.parametrize("week_start", [0, 6])
@pytest.mark.parametrize("T", [28, 31])
def test_weekly_holiday_windows(start_weekday: int, week_start: int, T: int) -> None:
    I = m.synthetic(T=T)
    I = dc.replace(I, start_weekday=start_weekday,
                   rules=dc.replace(I.rules, law=dc.replace(I.rules.law, week_start=week_start)))
    H = I.history.shape[1]
    wins = m.holiday_windows(I)
    wd = lambda t: (start_weekday + t - H) % 7
    covered = set()
    for t0, t1, k in wins:
        assert t1 - t0 == 7 and k == 1 and wd(t0) == week_start      # 7日・週の起算曜日から
        assert 0 <= t0 and t1 <= H + T and t1 > H                    # 範囲内で計画日にかかる
        covered |= set(range(t0, t1))
    # 範囲内に完全に収まる週に属する計画日は、必ずどれかの窓に入る
    for t in range(H, H + T):
        s0 = t - ((wd(t) - week_start) % 7)
        if s0 >= 0 and s0 + 7 <= H + T:
            assert t in covered, t


def test_four_week_windows() -> None:
    I = m.synthetic(T=31)
    I = dc.replace(I, rules=dc.replace(I.rules, law=dc.replace(I.rules.law, holiday_rule="4w4d", four_week_origin=2)))
    H = I.history.shape[1]
    assert m.holiday_windows(I) == [(H + 2, H + 30, 4)]


def test_param_validation_at_construction() -> None:
    """不正なパラメータは使う時点ではなく生成時に検出する"""
    with pytest.raises(ValueError):
        m.LawParams(holiday_rule="x")
    with pytest.raises(ValueError):
        m.LawParams(week_start=7)
    with pytest.raises(ValueError):
        m.DomainParams(admission_class="13:1")
    with pytest.raises(ValueError):
        m.constraint("bad_scope", "L1", scope="nowhere")
    with pytest.raises(KeyError):
        m.synthetic().idx("存在しない勤務")


def test_night_hour_bounds_do_not_assume_night_band() -> None:
    """72h の算入判定の界は night_band が有効なときだけ帯を前提にし、オフなら自明な界（下界 0）にする"""
    I, eps = m.synthetic(), m.Eps()
    u = {s: m.night_units(I, s) for s in range(len(I.shifts))}

    def bounds(inst: m.Instance, n: int) -> tuple[int, int]:
        M = m.NurseModel(inst, eps)
        M.build()
        return M._night_hour_bounds(n, [s for s in M.dom[n] if u[s] > 0], u)

    lb_on, ub_on = bounds(I, 0)
    J = dc.replace(I, rules=dc.replace(I.rules, off=frozenset({"night_band"})))
    lb_off, ub_off = bounds(J, 0)
    assert lb_on >= m.inclusion_threshold(I, 0) > lb_off == 0      # 帯があれば算入確定、なければ不確定
    assert ub_off >= ub_on                                         # 界はゆるくなる（健全側）


def test_every_constraint_has_a_check_or_is_exempt() -> None:
    """制約を足したら検証器の検査も足す: 検査の無い制約は意図した例外だけ（E1）"""
    import verify
    exempt = {"struct", "history", "sym_break"}                     # extract が保証 / 探索を絞る技法
    assert set(verify.CHECKS) <= set(m.REGISTRY)
    assert set(m.REGISTRY) - set(verify.CHECKS) == exempt


def test_verify_skips_switched_off_constraints_but_not_mandatory_ones() -> None:
    import verify
    I, eps = m.synthetic(), m.Eps()
    A = [[I.idx("日")] * I.n_days for _ in range(I.n_nurses)]       # 全員が毎日「日」: 公休数・休日などが違反
    assert any(e.startswith("72h") or "公休" in e for e in verify.check(I, eps, A))
    assert not any("公休" in e for e in verify.check(I, eps, A, off={"offcount"}))
    with pytest.raises(ValueError):
        verify.check(I, eps, A, off={"holiday"})


def test_solve_result_unpacks_like_a_tuple() -> None:
    I, eps = m.synthetic(), m.Eps()
    res = m.solve(I, eps, time_limit=30)
    ok, status, M, times, counts = res                              # 従来の展開
    assert res.ok is ok and res.model is M and set(times) == {"build", "transform", "solve"}
    assert counts["holiday"] == res.counts["holiday"] > 0


def test_weekend_pairs_and_shared_solution_helpers() -> None:
    I = m.synthetic(T=31)                                           # 計画日0=月 → 土 5,12,19,26 / 日 6,13,20,27
    assert m.weekend_pairs(I) == [(5, 6), (12, 13), (19, 20), (26, 27)]
    J = dc.replace(m.synthetic(T=28), start_weekday=5)             # 計画日0=土, 最終日(27)=金。土27日はなし
    assert all(sun < J.n_days for _, sun in m.weekend_pairs(J))
    K = dc.replace(m.synthetic(T=28), start_weekday=6)             # 計画日0=日 → 最終日27=土 は日曜が計画外で数えない
    assert 27 not in [sat for sat, _ in m.weekend_pairs(K)]
