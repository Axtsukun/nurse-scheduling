# クラス図（コア部分）

エンジンのコア部分を、**IF（入口・設定・戻り値）・データモデル・数理モデル・求解エンジン**の4つの領域に分けて、UML のクラス図（Mermaid）で示す。
モジュール間の依存は README 3.4 節、処理の流れ（シーケンス図）は README 3.1 節にある。

- 図は、`nurse/` と、トップレベルの `nurse_csp.py`・`verify.py`・`evaluate.py` のソースから取り出した名前（クラス・フィールド・メソッド・戻り値）に基づく。構成を変えたときは、この図も直す。`tests/test_docs.py` が、図とコードの一致を検査する。
- Python には UML のクラスにあたるもの以外（モジュール内の関数、データクラス、列挙型）があるので、ステレオタイプで区別する（下の凡例）。
- Mermaid の v10.9.1 と v11.17.2 で、5枚とも描画できることを確認している（GitHub が使う版は未確認）。

## 凡例

| 記法 | 意味 |
|---|---|
| `<<module>>` | モジュール。関数や定数の集まり（UML ではクラスの形で表す） |
| `<<dataclass>>` / `<<frozen dataclass>>` | データクラス（`frozen` は値を変更できない） |
| `<<enumeration>>` | 列挙型（`ReturnCode`） |
| `<<NamedTuple>>` | 名前つきタプル（`SolveResult`） |
| `<<mixin>>` | ミックスイン（機能を足すための継承用のクラス） |
| `<<external>>` / `<<builtin>>` | 外部ライブラリ / Python の組み込み |
| `<<type>>` | 型の別名（`Assignment`） |
| 白抜き三角の実線（`--|>`） | 継承（汎化） |
| 黒ひし形の実線（`*--`） | コンポジション（全体が部分を所有し、一緒に生成・破棄される） |
| 白ひし形の実線（`o--`） | 集約（部分を持つが、所有はしない） |
| 矢印の実線（`-->`） | 関連（フィールドとして持つ） |
| 矢印の破線（`..>`） | 依存（呼び出す・生成する・使う） |
| `1`・`0..*`・`1..*` | 多重度（個数の制約） |

括弧つきで示すメンバーのうち、`n_nurses`・`n_history`・`code`・`groups`・`given_items`・`soft_items`・`message`・`line`・`label`・`is_error` は、プロパティ（呼び出しに括弧は要らない）。

## 図1 全体像

4つの領域の主なクラスと、領域をまたぐ関係だけを示す。領域の名前は、`IF`（入口・設定・戻り値）・`DataModel`（データモデル）・`MathModel`（数理モデル）・`SolverEngine`（求解エンジン）。

```mermaid
classDiagram
    direction LR
    namespace IF {
        class nurse_csp {
            <<module>>
            +main(argv) int
        }
        class Settings {
            <<dataclass>>
        }
        class ReturnCode {
            <<enumeration>>
        }
    }
    namespace DataModel {
        class Instance {
            <<dataclass>>
        }
        class Rules {
            <<dataclass>>
        }
        class Eps {
            <<dataclass>>
        }
    }
    namespace MathModel {
        class NurseModel
        class ModelKernel
        class Named {
            <<dataclass>>
        }
    }
    namespace SolverEngine {
        class solver {
            <<module>>
            +solve() SolveResult
        }
        class diagnosis {
            <<module>>
            +diagnose() Diagnosis
        }
        class SolveResult {
            <<NamedTuple>>
        }
        class Diagnosis {
            <<dataclass>>
        }
    }
    nurse_csp ..> solver : solve
    nurse_csp ..> diagnosis : diagnose
    nurse_csp ..> Settings : load_settings
    nurse_csp ..> ReturnCode : 終了コード
    Instance *-- Rules
    NurseModel --|> ModelKernel : ミックスイン経由
    ModelKernel --> Instance : I
    ModelKernel --> Eps : eps
    ModelKernel ..> Named : build_named
    solver ..> NurseModel : 生成
    solver ..> SolveResult : 返す
    solver ..> Settings : 既定の時間制限
    SolveResult o-- NurseModel : model
    diagnosis ..> solver : solve
    diagnosis ..> Settings : 既定の時間制限
    diagnosis ..> Diagnosis : 返す
    Diagnosis o-- Named : items
    Diagnosis ..> ReturnCode : code
```

- **IF** は、入口（`nurse_csp`）、設定（`Settings`）、結果の戻り値（`ReturnCode`）を持つ。
- **データモデル** は、入力データ `Instance`（層ごとの規則 `Rules` を持つ）と、ε 制約の閾値 `Eps`。
- **数理モデル** は、`Instance` と `Eps` から制約の式を作る。`NurseModel` は、`ModelKernel` を継承した3つのミックスイン（図3）を合成したクラス。制約は `Named`（ID・クラス名・メッセージ）にして返す。
- **求解エンジン** は、`solver.solve` が `NurseModel` を作って解き、`SolveResult` を返す。`diagnosis.diagnose` は、解けないときに `solve` を呼び、原因を `Diagnosis` にまとめる。

## 図2 データモデル

```mermaid
classDiagram
    direction LR
    class Instance {
        <<dataclass>>
        +shifts list~ShiftDef~
        +domain list~list~int~~
        +n_days int
        +start_weekday int
        +history ndarray
        +demand list~list~tuple~~
        +seniors list~int~
        +leader_cats tuple~str~
        +night_cats tuple~str~
        +night_avg float
        +off_days list~int~
        +fixed dict~tuple, int~
        +rules Rules
        +night_only list~int~
        +short_time list~int~
        +incl_threshold dict~int, int~
        +req_off dict~int, list~
        +req_shift dict~int, list~
        +start_date date
        +nurse_names list~str~
        +cat_names dict~str, str~
        +n_nurses() int
        +n_history() int
        +idx(name) int
    }
    class ShiftDef {
        <<frozen dataclass>>
        +name str
        +start int
        +end int
        +cat str
        +is_work bool
        +fixed_only bool
    }
    class Rules {
        <<frozen dataclass>>
        +law LawParams
        +domain DomainParams
        +guideline GuidelineParams
        +off frozenset~str~
    }
    class LawParams {
        <<frozen dataclass>>
        +holiday_rule str
        +week_start int
        +four_week_origin int
        +holiday_strict_calendar bool
    }
    class DomainParams {
        <<frozen dataclass>>
        +night_window tuple~int, int~
        +handover dict~int, int~
        +limit_units int
        +budget_units int
        +admission_class str
        +incl_slots dict~str, int~
        +short_time_slots int
        +theta() int
    }
    class GuidelineParams {
        <<frozen dataclass>>
        +min_rest int
        +max_consec_work int
        +min_off_block int
        +max_work_weekends int
    }
    class Eps {
        <<dataclass>>
        +night_delta int
        +weekend_spread_regular int
        +weekend_spread_part int
        +req_viol_ratio float
        +symmetry_break str
    }
    class Assignment {
        <<type>>
        list of list of int
    }
    Instance "1" *-- "1..*" ShiftDef : shifts
    Instance "1" *-- "1" Rules : rules
    Rules "1" *-- "1" LawParams : law
    Rules "1" *-- "1" DomainParams : domain
    Rules "1" *-- "1" GuidelineParams : guideline
    note for Assignment "A[n][d] は、看護師 n の計画日 d の勤務の添字（Instance.shifts の位置）"
```

- `Instance` が入力のすべてを持つ。看護師・勤務・日付を整数の添字で表す（例: 勤務は `shifts` の位置）。
- `Instance` は、`ShiftDef` を複数（`shifts`）と、`Rules`（層ごとの規則のパラメータ）を1つ所有する。`Rules` は、`LawParams`（層1 労働法規）・`DomainParams`（層2 病院・看護職共通）・`GuidelineParams`（層3 努力義務）を所有し、`off`（外す制約名）を持つ。
- `Eps` は ε 制約の閾値で、`Instance` とは別に渡す。
- `Assignment` は解（割当表）の型で、`A[n][d]` が看護師 `n` の計画日 `d` の勤務の添字（`shifts` の位置）。

| `Instance` のフィールドの分類 | フィールド |
|---|---|
| 勤務 | `shifts`、`domain`（看護師ごとの取れる勤務の添字） |
| 計画 | `n_days`、`start_weekday`、`start_date`、`history`（前月末の実績）、`demand`（日別の需要区間 `(a, b, r)`。番ごとに一定で、1日を空きなく覆う） |
| 看護師の属性 | `seniors`（夜勤リーダー資格者）、`night_only`（夜勤専従者）、`short_time`（短時間正職員）、`incl_threshold`（72時間ルールの算入閾値の個別上書き）、`off_days`（公休数）、`nurse_names` |
| 規則・基準 | `rules`、`leader_cats`、`night_cats`、`night_avg` |
| 個別の入力 | `fixed`（固定割当）、`req_off`（休み要望。希望休を含む）・`req_shift`（シフト要望） |
| 表示 | `cat_names`（勤務区分名の上書き） |

## 図3 数理モデル

```mermaid
classDiagram
    direction TB
    class ModelKernel {
        +I Instance
        +eps Eps
        +H int
        +T int
        +N range
        +OFF int
        +dom list
        +col list
        +xv list
        +active frozenset~str~
        +t(d) int
        +x(n, t, s) Expression | None
        +x_taken(n, t, s) Expression
        +xs(n, t, ss) list
        +work(n, t) Expression
        +wvec(n) NDVarArray
        +cat(n, t, ks) list
        +night_count(n) Expression
        +is_active(name) bool
        +build_named(enabled) dict
        +build(enabled) dict
        +c_struct() Constraints
        +c_history() Constraints
        +c_fixed() Constraints
    }
    class LawConstraints {
        <<mixin>>
        +c_holiday() Constraints
    }
    class DomainConstraints {
        <<mixin>>
        +c_offcount() Constraints
        +c_cover() Constraints
        +c_skill() Constraints
        +c_72h() Constraints
    }
    class OptionalConstraints {
        <<mixin>>
        +c_rest() Constraints
        +c_consec() Constraints
        +c_offblock() Constraints
        +c_weekend() Constraints
        +c_night_band() Constraints
        +c_weekend_spread() Constraints
        +c_sym_break() Constraints
        +c_req_off() Constraints
        +c_req_shift() Constraints
    }
    class NurseModel
    class registry {
        <<module>>
        +REGISTRY dict~str, ConstraintSpec~
        +constraint(name, layer, relaxable, scope, source, title)
        +selected_constraints(I, enabled) list~str~
        +toggleable(names) list~str~
    }
    class ConstraintSpec {
        <<frozen dataclass>>
        +name str
        +layer str
        +relaxable bool
        +method str
        +scope str
        +source tuple~str~
        +title str
    }
    class Item {
        <<frozen dataclass>>
        +expr Expression
        +target tuple
        +params tuple~Param~
    }
    class Aux {
        <<frozen dataclass>>
        +expr Expression
    }
    class Named {
        <<frozen dataclass>>
        +id str
        +group str
        +title str
        +expr Expression
        +hard bool
        +target tuple
        +params tuple~Param~
        +ctx Instance
        +message() str
        +line() str
    }
    class Renderer {
        +I Instance
        +date(d) str
        +stamp(abs_slot) str
        +clock(slot) str
        +part(p) str
        +param(p) str
        +message(target, params) str
    }
    class Param {
        <<frozen dataclass>>
        +name str
        +value object
        +unit str
        +op str
    }
    class preprocess {
        <<module>>
        +night_units(I, s) int
        +rest_cliques(I) list
        +overnight_shifts(I) list
        +holiday_windows(I) list
        +cover_spans(I, d) list
        +cover_intervals(I, d) list
        +symmetry_classes(I, mode) list
        +precheck(I, eps) list
    }
    class Instance
    class Eps
    LawConstraints --|> ModelKernel
    DomainConstraints --|> ModelKernel
    OptionalConstraints --|> ModelKernel
    NurseModel --|> LawConstraints
    NurseModel --|> DomainConstraints
    NurseModel --|> OptionalConstraints
    ModelKernel --> Instance : I
    ModelKernel --> Eps : eps
    ModelKernel ..> registry : 有効な制約を選ぶ
    ModelKernel ..> Named : 生成
    ModelKernel ..> Item : 制約メソッドが返す
    ModelKernel ..> Aux : 制約メソッドが返す
    registry "1" o-- "0..*" ConstraintSpec : REGISTRY
    Item "1" o-- "0..*" Param : params
    Named "1" o-- "0..*" Param : params
    Named --> Instance : ctx
    Named ..> Renderer : message で使う
    Renderer --> Instance : I
    LawConstraints ..> preprocess
    DomainConstraints ..> preprocess
    OptionalConstraints ..> preprocess
    note for Renderer "対象の部品: Nurse, Day, DaySpan, Span, Cat, Shift, Clock（いずれも値オブジェクト）"
```

- **`ModelKernel`** は、決定変数 `x[n,t,s]`（`xv`。看護師 `n` の取れる勤務 `s` のみ作る疎な形）と、式の部品（`x`・`work`・`night_count` など）、常に有効な構造制約（`c_struct`・`c_history`・`c_fixed`）を持つ。`build_named` が、有効な制約ごとに `Named` の一覧を作り、`build` は式だけを返す（求解用）。
- **3つのミックスイン** `LawConstraints`（層1）・`DomainConstraints`（層2）・`OptionalConstraints`（層3）が、層ごとの制約メソッド（`c_` で始まる）を持つ。`NurseModel` は、3つを合成した（多重継承）クラスで、自分では何も定義しない。
- **登録の仕組み**: 制約メソッドは `@constraint` で `registry.REGISTRY`（制約名 → `ConstraintSpec`）に登録される。`selected_constraints` が、`Rules.off` と `enabled` を反映して有効な制約名を選ぶ。
- **制約の名前**: 制約メソッドは、式を `Item`（対象・パラメータつきの個別の規則）か `Aux`（補助変数の定義）で包んで返す。`build_named` が、全制約に ID を付けて `Named` にする。`Named.message` は、`Renderer`（日付・看護師名・区分名の表示方針を `Instance` から取る）が、診断のときだけ作る。
- `preprocess` は、窓・係数・集合を作る純 Python の関数群で、ソルバーに依存しない（`law`・`domain`・`optional` が使う）。

## 図4 求解エンジン

```mermaid
classDiagram
    direction LR
    class solver {
        <<module>>
        +SOLVE_SOLVER str
        +solve(I, eps, time_limit, workers, lin_level, solver, enabled) SolveResult
        +roster(M) list~str~
    }
    class SolveResult {
        <<NamedTuple>>
        +ok bool
        +status object
        +model NurseModel
        +times dict~str, float~
        +counts dict~str, int~
    }
    class diagnosis {
        <<module>>
        +MUS_SOLVER str
        +diagnose(I, eps, time_limit, mus_solver, mus_time_limit) Diagnosis
        +explain(d) str
        -_check(exprs, time_limit) str
        -_bounded_mus(soft, hard, solver, budget)
        -_mus_in_child(soft, hard, solver, budget)
    }
    class Diagnosis {
        <<dataclass>>
        +status str
        +detail str
        +items list~Named~
        +hard_only bool
        +times dict~str, float~
        +mus_solver str
        +minimal bool
        +code() ReturnCode
        +groups() list~str~
        +given_items() list~Named~
        +soft_items() list~Named~
        +explain() str
    }
    class NurseModel
    class Named
    class ReturnCode {
        <<enumeration>>
    }
    class Settings {
        <<dataclass>>
    }
    class CPSAT {
        <<external>>
        cpmpy + OR-Tools CP-SAT
    }
    class Exact {
        <<external>>
        PB ソルバー exact
    }
    solver ..> NurseModel : 生成して build
    solver ..> CPSAT : 求解
    solver ..> Settings : 既定の時間制限
    solver ..> SolveResult : 返す
    SolveResult o-- NurseModel : model
    diagnosis ..> solver : solve
    diagnosis ..> CPSAT : 充足判定
    diagnosis ..> Exact : 事前候補と MUS
    diagnosis ..> Settings : 既定の時間制限
    diagnosis ..> Diagnosis : 返す
    Diagnosis "1" o-- "0..*" Named : items
    Diagnosis ..> ReturnCode : code
```

- **`solver.solve`** は、`NurseModel` を作って式を生成し、CP-SAT（外部）で解き、`SolveResult` を返す。`SolveResult.model` は、解の読み出しに使う `NurseModel`。時間制限の既定値は `Settings`（`nurse.properties`）から読む。
- **`diagnosis.diagnose`** は、`solve` を呼び、解けなければ原因（時間切れ / 制約矛盾）を調べる。矛盾なら、事前候補（コア）と MUS を exact（外部）で、時間制限つきで求める（`_bounded_mus`・`_mus_in_child`）。結果は `Diagnosis`。
- **`Diagnosis`** は、`items`（矛盾の原因の `Named`）、`minimal`（最小か。時間制限で打ち切ると `False`）、`code`（`ReturnCode`）を持つ。`explain()` が日本語で説明する。

## 図5 IF（入口・設定・戻り値）

```mermaid
classDiagram
    direction LR
    class nurse_csp {
        <<module>>
        +run(argv) ReturnCode
        +main(argv) int
    }
    class config {
        <<module>>
        +DEFAULT_PATH Path
        +ENV_VAR str
        +parse_properties(text, source) Settings
        +load_settings(path) Settings
    }
    class Settings {
        <<frozen dataclass>>
        +solve_time_limit float
        +mus_time_limit float
    }
    class ReturnCode {
        <<enumeration>>
        OK = 0
        SOLVE_TIME_LIMIT = 10
        CONFLICT = 20
        CONFLICT_MUS_TIME_LIMIT = 21
        CONFLICT_UNIDENTIFIED = 22
        INPUT_ERROR = 30
        SOLVER_UNAVAILABLE = 40
        INTERNAL_ERROR = 50
        +label() str
        +is_error() bool
    }
    class NurseError {
        +code ReturnCode
    }
    class InputError
    class SolverUnavailableError
    class InternalError
    class ValueError {
        <<builtin>>
    }
    class RuntimeError {
        <<builtin>>
    }
    class verify {
        <<module>>
        +extract(M) Assignment
        +check(I, eps, A, off) list~str~
        +report(I, A) dict
    }
    class evaluate {
        <<module>>
        +evaluate(I, A) dict
        +print_report(I, ev)
    }
    class Assignment {
        <<type>>
        list of list of int
    }
    class solver {
        <<module>>
    }
    class diagnosis {
        <<module>>
    }
    nurse_csp ..> config : load_settings
    nurse_csp ..> solver : solve
    nurse_csp ..> diagnosis : diagnose
    nurse_csp ..> ReturnCode : 終了コード
    config ..> Settings : 返す
    config ..> InputError : 不正な設定
    NurseError <|-- InputError
    NurseError <|-- SolverUnavailableError
    NurseError <|-- InternalError
    ValueError <|-- InputError
    RuntimeError <|-- SolverUnavailableError
    RuntimeError <|-- InternalError
    NurseError ..> ReturnCode : code
    verify ..> Assignment
    evaluate ..> Assignment
    evaluate ..> verify
```

- **`nurse_csp`**（CLI）は、`config.load_settings` で設定を読み、`solver.solve`・`diagnosis.diagnose` を呼び、`ReturnCode` を終了コードにする。
- **`config`** は、`nurse.properties`（Java 形式）を `Settings`（`solve_time_limit`・`mus_time_limit`）にする。不正な設定は `InputError`。
- **`ReturnCode`** は、0（正常）・10 台（時間打ち切り）・20 台（制約矛盾）・30 以上（異常）。例外は `NurseError` を基底とし、`InputError`（`ValueError` の一種）・`SolverUnavailableError`・`InternalError`（どちらも `RuntimeError` の一種）が、それぞれ対応するコードを持つ。
- **`verify`**（独立検証器）と **`evaluate`**（集計）は、解 `Assignment` を読む側で、モデルの式には依存しない。

## 図に含めないもの
`message` の部品（`Nurse`・`Day`・`DaySpan`・`Span`・`Cat`・`Shift`・`Clock`。図3 の注記のみ）、`solution` の関数、`synthetic`、`verify` の個別の検査関数、各クラスの非公開メソッド（`_` で始まるもの。図4 の `diagnosis` の主な内部関数を除く）。

## 用語
- **クラス図**: クラスの持つ値・操作と、クラス同士の関係を示す UML の図です。
- **ステレオタイプ**: `<<…>>` で書く、クラスの種類の注記です。
- **コンポジション・集約・関連・依存**: 関係の強さの順に、所有（一緒に生成・破棄される）、保持（所有はしない）、フィールドとして参照、使う（呼ぶ・生成する）です。
- **ミックスイン**: 他のクラスに機能を足すための継承用のクラスです。
- **MUS**: 同時には成り立たない制約の最小の集合です。
