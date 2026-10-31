# ナーススケジューリング CSP エンジン

CPMpy + OR-Tools CP-SAT による看護師勤務表作成エンジン。純PB（Boolean変数＋整数係数線形制約）の CSP として解く。

## ファイル構成
```
nurse/                  # エンジン本体（依存は下向きのみ）
  instance.py           # 入力データ・層ごとのパラメータ（LawParams/DomainParams/GuidelineParams/Rules）・単純な問い合わせ
  solution.py           # 解（割当表）の読み取り部品（検証器と評価が共有）
  preprocess.py         # 時刻データから係数・集合・窓を作る純Python（ソルバー不要でテストできる）
  message.py            # 制約メッセージの構造化データ（対象・パラメータ）と整形器 Renderer（日付表記・看護師名・区分名）
  registry.py           # @constraint の登録、名前つき制約（Item/Aux/Named。ID・クラス名・メッセージ）、有効な制約の選択
  kernel.py             # 変数 x と式の部品、構造制約（scope=core）
  law.py / domain.py / optional.py   # 層1 / 層2 / 層3 の制約（ModelKernel を継承したミックスイン）
  model.py              # NurseModel（上の合成）
  solver.py             # solve（SolveResult）・roster
  diagnosis.py          # 解けないときの診断（時間切れ / 制約矛盾 → MUS を exact で求め、日本語で説明）
  synthetic.py          # 合成インスタンス・個別要望の付与
nurse_csp.py            # 互換の窓口（import nurse_csp as m）と CLI
verify.py               # 独立検証器（制約名ごとの検査関数 CHECKS）
evaluate.py             # 解の評価（集計と表示）
bench_*.py              # 計測スクリプト
docs/legal/             # 法令・基準の調査（制約の根拠）
docs/design/            # 設計の仕様（制約名・メッセージの仕様 constraint-message-spec.md）
tests/
```

## 制約の足し方
1. 該当する層のモジュール（`law.py` / `domain.py` / `optional.py`）に、`@constraint(名前, layer, relaxable, scope, source, title)` を付けたメソッドを書く。`list[Expression | Item | Aux]` を返し、補助変数はその中で作る。個別の規則は `Item(式, 対象, パラメータ)`（文字列は組み立てない。`message.py` の部品 `Nurse`・`Day`・`DaySpan`・`Span`・`Cat`・`Shift`・`Clock` と `Param` を使う）、補助変数の定義式は `Aux(式)` で包む。`title` は短い制約クラス名（片側の群は方向を含む。例: `人数制約下限`）。全 soft 制約がメッセージを持つことは tests/test_named.py が検査する。
2. パラメータは層ごとのデータクラス（`instance.py`）に足す。生成時に検証する（`__post_init__`）。
3. `verify.py` に同名の検査関数を書き、`CHECKS` に登録する（式を使わず、元の規則から直接数える）。対応漏れは `tests/test_layers.py` が検出する。
4. 前処理（窓・係数）が必要なら `preprocess.py` に純Pythonで書き、総当たりと照合するテストを `tests/` に足す。
5. 根拠があれば `docs/legal/constraints.yaml` に id を足し、`source` に指定する。
6. 他の制約を前提にした導出（例: 72h の界が night_band を前提とする）をするときは、`self.is_active(名前)` で前提が有効か確かめ、無効なら前提を使わない式にする。

## 設計方針（変更する場合はユーザーに確認）
- **目的関数なし（CSP）**。品質要件は ε 制約（閾値付き制約）で表す。計算時間の最小化を優先する。
- **純PB**：決定変数・補助変数はすべて Boolean。`cp.intvar` は使わない。係数は整数のみ。
- **時刻は30分単位の整数**。0=当日0:00、48=翌日0:00。勤務は開始した暦日に属する（深夜0:30開始は start=1）。
- **勤務の値はデータ**（`ShiftDef`）。禁止遷移・人数制約・夜勤時間はすべて時刻から生成する。勤務名をコードに直書きしない（`"休"` のみ例外）。
- **x は疎**：看護師 n の取れる値 D_n についてのみ変数を作る（`M.xv[n]`、`M.col[n]`）。
- **制約は1定義＝1メソッド**：`@constraint(name, layer, relaxable, scope, source, title)` で登録し、`list[Expression | Item | Aux]` を返すだけ（副作用なし）。補助変数はそのメソッド内で生成する。
- **制約の名前（決定事項。仕様は docs/design/constraint-message-spec.md）**：CPMpy は制約式に名前を付けられない。式を `Item`（対象・パラメータ）/ `Aux`（補助変数の定義。hard）で包み、`build_named()` が `Named` にする。`Named` は **ID**（`{群}-{群内連番4桁}`。全制約に付け、同じ入力なら同じ ID。例 `cover-0012`）・群・**クラス名**（日本語・短い）・**メッセージ**を持つ。メッセージは `<対象> <名前>=<値>` で、構造化データから整形器が**診断のときだけ**作る（`build()` は文字列を作らない）。表示行は `{ID}  [{クラス名}] {メッセージ}`（例 `cover-0012  [人数制約下限] 11/03 12:30 - 11/03 13:00 勤務者数=10`）。日付は `Instance.start_date` があれば `MM/DD HH:mm`、なければ `N日目(曜)`。看護師名・区分名は `Instance.nurse_names`・`cat_names`。`build()` は式だけを返す（求解用。式は名前の有無で変わらない）。
- **層構成**（`scope`）：`core`（カーネル）／`law`（層1 労働法規）／`domain`（層2 病院・看護職共通）／`optional`（層3 努力義務・要望）。core と law はオフにできない（`selected_constraints` が `ValueError`）。層2・3は `Rules.off` で外す。パラメータは層ごとに `LawParams` / `DomainParams` / `GuidelineParams`、まとめて `Instance.rules`。`layer`（L1〜L5）は式の作り方の分類で、`scope` とは別の軸。
- **ソルバーの選択（決定事項）**：**求解は CP-SAT**（`solver.SOLVE_SOLVER = "ortools"`）、**MUS の計算は exact**（`diagnosis.MUS_SOLVER = "exact"`）。実測（README 4.5 節）の根拠あり。exact が使えないときは黙って別のソルバーに替えず、エラーにする。`solve(solver=...)` と `diagnose(mus_solver=...)` の引数は実験用で、既定値を変えるときはユーザーに確認する。
- **診断は別モデル**：通常求解に指示変数を入れない。解けない原因は「時間切れ」と「制約矛盾」の2つ。`diagnose()` はまず通常モデルを CP-SAT で解き、実行不能と確定したときだけ `cpmpy.tools.mus` を **exact**（PBソルバー）で使う。実測（README 4.5 節）では、14条件すべてで CP-SAT の1.2〜3.3倍速い（中央値1.6倍）。`mus` に時間制限が無いので、平均値の制約（72時間ルール）を極端に締めた矛盾などでは `diagnose` が戻らないことがある。
  - **hard / soft の分け方**（MUS に別々に渡す）: 背景（構造・履歴・補助変数の定義。`relaxable=False` と `Aux`）と前提（カーネル・層1 = `MANDATORY`。固定割当・労基法の休日）が hard、層2・層3（`TOGGLEABLE`）が soft。
  - **手順**: ①前提だけで矛盾するなら、前提の中から MUS（`hard_only=True`: 入力データと法令の矛盾）。②そうでなければ soft の MUS を**一段階**で求め（制約群を単位にした先行の絞り込みは、実測で12/12条件で一段階より遅かったので採用しない）、③その矛盾に関わる前提だけを追加で絞る。
  - 結果は `Diagnosis`（status は feasible / timeout / conflict。`items` / `soft_items` / `given_items`）で、`explain()` が日本語で説明する。時間切れでは MUS を求めない（矛盾かどうか不明なため）。
- **パターン化（週パターン列挙）は採用しない**（規模が約100倍、CSPではLP緩和の利点が活きない）。

## 前提と教訓（メモ。入力の定義と設計の判断を誤らないために）
- **人数制約の需要は番ごとに一定**（日勤・準夜・深夜などの時間帯に、1つの人数）。30分スロットごとに可変の需要が入力されるという前提を置かない。
  番の中の実際の在席数は、勤務パターン（始業・終業のずれ、短時間勤務、前日からの持ち越し）で増減する。現行の制約は「どの30分スロットも在席数 ≥ 要求」（不足は許さず、上ぶれは許す）。基本区間に分けるのは、在席する顔ぶれが番の途中で変わるためで、需要が可変だからではない。
  → 「同じ被覆で要求が違う区間を最大値で統合する」処理（`cover_spans` の統合）は、30分ごとに可変の需要という誤った前提への対応で、過剰設計。現実の入力（番ごとに一定）では統合は0件で、本数を減らすのは支配除去だけ（30日で540 → 330本）。
- **入力データの粒度・意味が定義されていないときは、勝手に前提を置かない**。人工的な入力（需要の空き、30分ごとの変化）で問題を作って対処を設計しない。まず入力の定義をユーザーに確認し、現実のデータで起きることを確かめてから設計する。
- 方針の整理だけを求められたときはコードを変えない（変更はユーザーの指示を待つ）。

## 表記規約
- 数式記号はコメント・docstring で `x[n,t,s]`, `S_n`, `θ_n` のように書く。識別子とは区別する。
- 主な対応：x[n,t,s] → `M.x(n,t,s)`（実体 `M.xv[n][t, M.col[n][s]]`）、w[n,t] → `M.work(n,t)`（式）、
  u_s → `night_units(I,s)`、(A_θ,B_θ) → `rest_cliques(I)`、(C_j,r_j) → `cover_intervals(I,d)`、
  z_n → `z`、y[n,d,s] → `y`（片側定義、解の読み出しに使わない）。

## 主要な定式化（厳密性が前提。変更時は tests/test_preprocess.py を更新）
- 勤務間隔 ρ：閾値分解 `Σ_{A_θ} x[t] + Σ_{B_θ} x[t+1] ≤ 1`。禁止ペア集合と完全一致すること。
- 人数：基本区間 → 同一被覆統合 → 支配除去（C_i ⊂ C_j かつ r_i ≥ r_j なら j を除く）。30分スロット制約と同値であること。
- 72時間ルール：`Σ z_n(h_n − Λ) ≤ q`。除外は「θ未満」（7対1・10対1は16h未満、その他8h未満、短時間正職員12h未満）。θ は `inclusion_threshold(I, n)`（個別上書き → 短時間正職員 → `DomainParams.admission_class`）。夜勤専従者は除外。上下界で z を事前固定し、未確定者のみ z, y を生成。**界は night_band が有効なときだけ夜勤回数の帯から導く**（オフなら自明な界）。night_band をオフにすると 72h の補助変数が全員分に増え（制約 249本 → 7321本）、求解が大幅に遅くなる（30人・30日で 1.7秒 → 33秒、別の回では 120秒でも未解決。ばらつきが大きい）。
- 休日（層1, 労基法35条）：`holiday_windows(I)` の各窓 [t0,t1) に「休」が k 日以上（週の窓は k=1、4週の窓は k=4）。有休は休日に数えない。窓が計画の外にはみ出す端の週は検査できず対象外。`verify.check` は別の方法（週・ブロックへの分割）で独立に検査する。

## 検証
```bash
pip install -r requirements.txt
pytest -q tests/test_preprocess.py     # ソルバー不要：前処理の厳密性
pytest -q -s tests/test_solve.py       # 求解＋独立検証器（verify.py）
pytest -q tests/test_layers.py         # 層構成・根拠ID・休日の窓・検査関数の対応（一部は求解する）
pytest -q                              # 全体（約1分40秒。層2・層3の制約を1つずつ外す求解、MUS の計算を含む）
python nurse_csp.py                    # 合成インスタンスで計測
```
- 解の正しさは必ず `verify.check()`（モデルの式を使わない独立検証器）で確認する。
- 計時は build / transform（CPMpy変換）/ solve を分けて報告する。

## 未確認事項（実行して確定させる）
- CPMpy API の細部：`solve(time_limit=..., num_workers=..., linearization_level=...)` の引数受け渡し、`solve(assumptions=...)` と `get_core()`、`SolverLookup.get("pysat")`（exact は MUS で動作確認済み）。
- 72時間ルールの計算単位（病棟か届出単位か）・計算期間。原典（告示・通知）で確認。
- 実データでの可解性（合成インスタンスは 30/60/90人・28〜31日で可解を確認済み）。実行不能なら `precheck()` と `diagnose()` の結果を見てデータ側を調整する。

## 法令・基準の調査（docs/legal/）
制約の根拠（労基法・診療報酬の施設基準・看護協会ガイドライン）は `docs/legal/` に整理済み（調査のみ、実装には未反映）。
制約を追加・変更する前に `docs/legal/constraints.yaml` を引き、確度が low の項目は原典を確認するまで実装の根拠にしない。
