# GraphMaker Python 3.9互換版

## 既存版との違い

このフォルダは、既存のPython 3.12版GraphMakerを参照して作成した独立版です。内部パッケージ名は互換性のため `tga_analyzer` のままですが、画面タイトル、ランチャー、パッケージ情報、設定保存先をPython 3.9互換版専用に分離しています。既存版のソース、デモデータ、設定、ZIPは読み取り対象とし、互換版から変更しません。

PyInstaller版EXEは作成していません。ソース版ランチャーだけを使用します。

## Python 3.9対応の変更

- `@dataclass(slots=True)` から `slots=True` を削除しました。
- `@dataclass(frozen=True, slots=True)` は `@dataclass(frozen=True)` とし、変更不可の意味を維持しました。
- `zip(..., strict=True)` は `tga_analyzer.compat.strict_zip` に置き換えました。
- `strict_zip` は処理前に全入力の長さを確認し、不一致時は呼出箇所と各配列長を含む `ValueError` を送出します。
- PEP 604形式の型注釈（`A | B`）は `typing.Union[A, B]` へ置き換えました。
- `match / case`、Python 3.10以降専用のtyping機能、`tomllib`、`itertools.pairwise`などの利用がないことを静的に確認しました。
- `pyproject.toml` の `requires-python` を `>=3.9` としました。

## 必要環境

- Windows
- Python 3.9以上（Tkinterを含む構成）
- `openpyxl==3.1.5`

新しい直接依存ライブラリは追加していません。CSV処理、解析、GUI、バックグラウンド処理にはPython標準ライブラリを使用します。`openpyxl==3.1.5`のパッケージメタデータはPython 3.8以上に対応しており、推移的依存関係として `et-xmlfile` が必要です。

## 起動方法

- 通常起動：`GraphMaker_Python39.pyw` をダブルクリック
- エラー確認：`GraphMaker_Python39.bat` をダブルクリック

ランチャー、`src`、`profiles`、`requirements.txt` の相対配置は変更しないでください。3.9未満のPythonでは、ランチャーが必要バージョンを表示して起動を中止します。

## 設定保存先

互換版の設定とユーザー読込プロファイルは、次の専用フォルダだけを使用します。

```text
%LOCALAPPDATA%\GraphMaker_Python39
```

既存版の `%LOCALAPPDATA%\GraphMaker` および旧版の `%LOCALAPPDATA%\TGAAnalyzer` は読み込みも変更も行いません。

## テスト結果

作成時点のPCにはPython 3.9が存在しないため、Python 3.9実機での起動・GUI操作は未確認です。

Python 3.14.3と `openpyxl==3.1.5` では、既存テストと追加互換性テストの合計187件がすべて成功しました。追加の非対話GUIスモークテストでも、6モードの分離、各モードのデモ読込、IR/DSC処理、粒度分布規格化、グラフウィンドウ、色・凡例名、Excelネイティブグラフ出力とopenpyxl再読込が成功しました。さらに、`GraphMaker_Python39.pyw`から実画面を起動し、専用タイトル、粒度分布モードへの切替、別グラフウィンドウの起動を確認しました。これらはPython 3.14上の結果であり、Python 3.9実機確認ではありません。

静的検査では、Python 3.9文法を指定したAST解析、dataclassのslots指定、strict付きzip、PEP 604型注釈、match/case、既知の新しい標準ライブラリAPIを確認します。静的検査はPython 3.9実機テストの代替ではありません。

## Python 3.9実機で必要な最終確認

1. Python 3.9と承認済みの `openpyxl==3.1.5` を用意します。
2. `python -m unittest discover -s tests -v` を実行します。
3. `GraphMaker_Python39.pyw`から起動します。
4. 全測定モードへの切替、各デモCSVの追加、グラフ表示、色・凡例名変更を確認します。
5. Excelへ出力し、保存したファイルをExcelおよびopenpyxlで再読込します。
6. 設定が `%LOCALAPPDATA%\GraphMaker_Python39` のみに保存されることを確認します。

## サポートとセキュリティ

Python 3.9は2025年10月31日に公式サポートを終了しています。セキュリティ修正を含む公式更新を受けられないため、社内利用前にセキュリティ担当へ次を確認してください。

- サポート終了済みPythonの利用可否と例外承認
- Python 3.9本体の入手元、改ざん検査、社内配布方法
- `openpyxl==3.1.5`と推移的依存関係の承認状況
- CSVおよびExcelファイルの取扱区分
- 共有フォルダ上での実行可否
- 将来のPython更新計画と脆弱性対応手順
