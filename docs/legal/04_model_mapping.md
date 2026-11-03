# 現行モデル（nurse_csp.py）との対応

| 法令・基準 | ID | 現行の制約名 | 状況 |
|---|---|---|---|
| 勤務間隔11時間 | GL-REST-11H | `rest`（`min_rest=22`） | 実装済み |
| 連続勤務5日以内 | GL-CONSEC-WORK | `consec`（`max_consec_work=5`） | 実装済み |
| 月平均夜勤72時間 | REIMB-72H, -EXCLUDE | `72h` | 実装済み（除外規定も反映） |
| 公休日数（月の総数） | （病院の取り決め） | `offcount`（層2） | 実装済み。法令の休日とは別 |
| 週ごとの休日・4週4休 | LAW-HOLIDAY | `holiday`（層1） | **実装済み**（`LawParams.holiday_rule`） |
| 日別必要人数・夜勤リーダー | REIMB-NIGHT-2（間接） | `cover`, `skill` | 実装済み |
| 夜勤回数の帯 | GL-NIGHT-COUNT | `night_band` | 部分的（月8回上限そのものではない） |
| 勤務週末数 | GL-WEEKEND（部分） | `weekend`, `weekend_spread` | 部分的 |
| 変形労働時間制の総労働時間 | LAW-HOURS-VAR1M | なし | **未実装** |
| 時間外労働の上限 | LAW-OVERTIME | なし | **未実装**（勤務を所定内に収めているため事実上発生しない） |
| 妊産婦・育児・介護の制限 | LAW-PREGNANT 他 | なし | **未実装**（`domain` で夜勤を除外すれば実装可能） |
| 夜勤2人以上（うち看護師1人以上） | REIMB-NIGHT-2 | `cover` で間接的 | 明示の制約なし |
| 連続夜勤2回まで | GL-CONSEC-NIGHT | なし | **未実装** |
| 夜勤後の休息 | GL-REST-AFTER-NIGHT | なし | **未実装** |
| 正循環 | GL-ROTATION | なし | **未実装** |
| 看護師比率・人員標準 | REIMB-RATIO, MED-STAFF-STD | なし | **未実装**（集計検査の対象） |

## 実装時の指針（案）
- 実装は `CLAUDE.md` の設計方針に従う（純PB、勤務名の直書きなし、1定義＝1メソッド）。
- 追加するたびに `tests/test_preprocess.py`（厳密性）と `verify.py`（独立検証）を更新する。
- 時間窓の制約（連続夜勤・夜勤後休息）は、現行の `rest` と同じく勤務の時刻データから生成する。
- 妊産婦・育児・介護の制限は、`Instance.domain` から夜勤の値を外すのが最小の変更。

## 層構成（実装済み。コードは `nurse/kernel.py` / `law.py` / `domain.py` / `optional.py`）
| 層 | 適用範囲（`scope`） | 制約 | パラメータ |
|---|---|---|---|
| カーネル | `core` | `struct`, `history`, `fixed` | — |
| 層1 労働法規 | `law` | `holiday` | `LawParams`（休日の規則、週の起算曜日、4週の起算日、暦日の厳密性） |
| 層2 病院・看護職共通 | `domain` | `cover`, `skill`, `offcount`, `72h` | `DomainParams`（夜勤時間帯、72h、入院基本料の区分、算入閾値） |
| 層3 任意 | `optional` | `rest`, `consec`, `offblock`, `weekend`, `weekend_spread`, `night_band`, `req_off`, `req_shift`, `sym_break` | `GuidelineParams`、`Eps` |

- カーネルと層1は `Rules.off` / `solve(enabled=...)` で外せない（外そうとすると `ValueError`）。
- 層2・層3は `Rules.off`（病院のプロファイル）または `enabled`（実験）で外せる。
- 制約は下の層に「加える」ので、上の層が下の層を弱めることはない。
- 各制約の `source`（根拠ID）は `constraints.yaml` の id と一致する（`tests/test_layers.py` が検査）。
