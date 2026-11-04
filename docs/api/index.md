# API リファレンス

ソースの docstring（関数・クラスの説明文）から自動生成している。コードを直せばここも変わるので、説明を直したいときは `.py` の docstring を直す。

| 区分 | モジュール |
|---|---|
| 入口 | `nurse_csp`（CLI・求解→診断）、`nurse_csv`（CSV で入出力する窓口。Web アプリとの IF）、`verify`（独立検証器）、`evaluate`（評価） |
| 入力・結果 | `nurse.instance`、`nurse.solution`、`nurse.codes`、`nurse.config`、`nurse.logger` |
| モデル | `nurse.model`、`nurse.kernel`、`nurse.law`、`nurse.domain`、`nurse.optional`、`nurse.registry`、`nurse.message`、`nurse.preprocess` |
| 求解・診断 | `nurse.solver`、`nurse.diagnosis` |
| 合成データ | `nurse.synthetic` |

構成と依存の関係は、トップの README 3 章とクラス図を見る。
