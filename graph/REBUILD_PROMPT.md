# GraphMaker 再構築指示書

## 0. このドキュメントの位置づけ

既存アプリ `GraphMaker_Python39` の **UI層を全面的に作り直す**ためのタスク指示書。
科学計算・解析ロジックは既に十分に検証されている（224テスト全成功）ため**移植して温存**し、
UIだけを современ な設計で作り直す。

**あなた（実装者）はこの文書を仕様として、フェーズ順に実装する。**

---

## 1. 絶対制約（違反禁止）

### 1.1 元ディレクトリは読み取り専用

```
C:\Users\giggl\OneDrive\ドキュメント\仕事効率化\GraphMaker_Python39
```

このディレクトリの**ファイルを一切変更・削除・移動しない**。参照とコピー元としてのみ使う。
新しい実装はすべて以下に作る:

```
C:\Users\giggl\claude_project\graph
```

### 1.2 動作環境（会社PC配布のための厳しい制約）

| 項目 | 制約 |
|---|---|
| Python | **3.9 以降で動作すること**（3.10+ 専用構文は禁止） |
| GUI | **tkinter / ttk のみ**。PyQt・Kivy・Web系は禁止 |
| 外部依存 | **`openpyxl==3.1.5` のみ**。numpy / pandas / matplotlib 禁止 |
| 描画 | グラフは **tkinter Canvas への自前描画**（matplotlib不可） |
| ネットワーク | **一切の外部通信を行わない** |
| 配布形態 | ソース版ランチャー（`.pyw` ダブルクリック起動）。EXE化しない |
| OS | Windows 10/11、日本語環境 |

Python 3.9 互換で特に注意すべき点:
- `X | Y` 型記法は**使わない** → `Union[X, Y]`（`from typing import Union`）
- `list[str]` / `dict[str, int]` は `from __future__ import annotations` があれば注釈内では可。
  ただし**実行時評価される箇所（`TypeVar` 境界、`cast`、dataclass の `field` 既定値等）では使わない**
- `match` 文、`zip(strict=)`、`itertools.pairwise` は禁止
  （既存の `compat.py` に `strict_zip` があるので使う）

### 1.3 出力互換性（製品の中核価値）

Excel出力の書式は**この製品の存在理由**（論文・報告書にそのまま貼れる図）。
現行の出力仕様を**1ミリも劣化させてはならない**:

- `Data` シートA1から全点データ、`D5`付近にExcelネイティブ編集可能な散布図
- グラフ約18×12 cm、グラフエリア・プロットエリアは白
- プロット枠・X軸線・Y軸線は**黒1.5 pt**、補助線なし、**内向き目盛**
- 軸タイトル **Arial 14 pt 太字**、目盛ラベル・凡例 **Arial 12 pt 標準**
- グラフタイトルなし、タイトル削除後の余白を詰めてプロット領域を拡大
- ヘッダー無装飾、列幅約7
- 系列名・色・表示順・軸範囲をアプリから引き継ぐ

→ `excel_export.py` は**そのまま移植**すること。書き換えない。

---

## 2. 事前調査で確定済みの事実（再調査不要）

### 2.1 規模

元コードは `src/tga_analyzer/` に 29モジュール・**13,389行**。
テストは `tests/` に 33ファイル・**224テスト、全成功**（実測確認済み）。

### 2.2 依存関係の調査結果 —— ここが最重要

import解析の結果、**純ロジック層とUI層が完全に分離している**ことが判明した。
`tkinter` を import しているのは以下 **7ファイルのみ**:

```
gui.py (4448)  graph_window.py (642)  plot_canvas.py (932)  ui_theme.py (67)
series_dialogs.py (497)  import_profile_dialog.py (766)  sample_info_dialog.py (257)
                                                          合計 約 7,600行 = 捨てる
```

残り **22ファイル・約5,800行は tkinter 非依存の純ロジック**。
これらは**そのままコピーして再利用する**。ここを書き直すと検証済みの数値計算を失う。

### 2.3 そのまま移植するファイル（**中身を書き換えない**）

```
model.py                      データモデル・9モード定義・PlotState・AxisRange
parser.py                     CSV解析
processing.py                 DSC/IR ブランク補正・0点・規格化
particle_size_processing.py   粒度分布 規格化
display_series.py             ★描画とExcelの共通境界 DisplaySeries
analysis_common.py            解析共通（XRange, AnalysisError）
tga_analysis.py               Td5/Td50/Td95/Tdx
dsc_analysis.py               Tg・融解解析
dsc_selection.py              DSC 4点選択の状態機械
generic_selection.py          汎用 点選択の状態機械
new_mode_analysis.py          温度ロガー・SSカーブ・粘着力の解析
value_readout.py              読取X の補間
series_edit.py                色・凡例名の正規化
result_copy.py                結果のTSVコピー
filesystem.py                 フォルダ・CSV列挙
settings.py                   設定JSON永続化
import_profiles.py (1173)     ★読込プロファイル機構（純ロジック・巨大）
excel_export.py               ★Excel出力（openpyxlのみ）
branding.py  compat.py  __init__.py
```

**あわせて `tests/` 33ファイルと `profiles/` と `demo_data/` もコピーする。**

### 2.4 作り直すファイル

`gui.py` / `graph_window.py` / `plot_canvas.py` / `ui_theme.py` /
`series_dialogs.py` / `import_profile_dialog.py` / `sample_info_dialog.py`

### 2.5 重要な設計上の接続点

- **`DisplaySeries`（`display_series.py`）が唯一の描画データ境界。**
  `to_display_series()` が生カーブ／処理済カーブ／解析派生カーブを統一形式に変換する。
  新しい描画エンジンも Excel 出力も**必ず `DisplaySeries` を入力とする**。
  `x_axis_title` / `reverse_x`（IR用）/ `logarithmic_x`（粒度分布用）/ `extra_columns` を持つ。
- 9モードは `model.MEASUREMENT_TYPES` で定義済み:
  `TGA / DSC / IR / UV-Vis / GPC / 粒度分布 / 温度ロガー / SSカーブ / 粘着力`
- モードごとに**読み込み済み曲線・表示順・色・凡例名・軸範囲・選択状態を独立保持**する仕様。

---

## 3. 現行UIの問題点（実機調査で確認済み）—— これらを全部潰す

実際にアプリを起動しTGAデータを読み込んでスクリーンショットを撮り、コードと突き合わせて確認した。

### 3.1 導線の問題

| # | 問題 | 根拠 |
|---|---|---|
| D1 | **2ウィンドウ往復**。設定はメイン画面、グラフ上のクリック解析は別ウィンドウ。DSC解析1回に何度も行き来する | `gui.py` の処理設定 vs `graph_window.py` の4点選択 |
| D2 | 「グラフを開く」ボタンが**同一画面に2箇所** | `gui.py:374` と `gui.py:513` |
| D3 | 「読み込み済み系列」の見出しが**2回連続で出る** | `gui.py:508` と `gui.py:520`（LabelFrame題名） |
| D4 | **系列表が2つ**存在し列構成も操作も別物。色・凡例名編集はグラフ窓側にしかない | `gui.py:524` と `graph_window.py:191` |
| D5 | **「解析結果」タブが嘘をつく**。9モード中TGAしか使わず、DSC等は「解析設定タブ内を見ろ」とメッセージ誘導 | `gui.py:583`, `gui.py:2442` |
| D6 | 軸範囲・読取X が**ツールバーとインスペクタに二重**に存在 | `graph_window.py:103` と `graph_window.py:260` |
| D7 | **モーダルポップアップ乱用**。`gui.py` だけで `messagebox` 82箇所。「フォルダを選択してください」程度でも作業が止まる | `gui.py:2675`, `gui.py:2681` ほか |
| D8 | DSC設定が**1行に11列分**の入力を詰め込み、余白ゼロ | `gui.py:806-863` |
| D9 | 数値補正欄が**幅7の入力6個横並び** | `gui.py:891-912` |

### 3.2 配色・スタイルの問題

| # | 問題 | 根拠 |
|---|---|---|
| C1 | **グラフ線色が matplotlib tab10 の直移植**（`#1F77B4,#D62728,…`）。研究者が見慣れた「デフォルト感」が最大の"ダサい"要因 | `model.py:32` `DEFAULT_PALETTE` |
| C2 | テーマ外の**似て非なる色**が散在。`#667085` `#475467` `#B42318` `#0E7490` 等がハードコード | `gui.py:429,1041,1162` `import_profile_dialog.py:175` |
| C3 | `tk.Canvas(background="#F4F7FA")` のようにテーマ値を**文字列リテラルで重複定義** | `gui.py:572` |
| C4 | 5つのToplevelが**自身の背景色を設定しておらず**、OS標準グレーの余白が透ける | `series_dialogs.py:53` `sample_info_dialog.py:11` ほか |
| C5 | `LabelFrame` の入れ子＋`Card.TFrame`の1px枠で**枠の中に枠**の視覚ノイズ | `gui.py:621,641` `graph_window.py:423,463` |
| C6 | フォントがほぼ全て **Segoe UI 9pt 一律**で情報の階層がない。日本語はフォールバック任せ | `ui_theme.py:36-66` |
| C7 | 高DPI対応なし。ぼやける／小さい | DPI awareness 呼び出しが存在しない |

---

## 4. 新UI設計

### 4.1 設計方針

> **ユーザーは「グラフを見ながら操作する」。グラフを隠してはならない。**

ユーザー承認済み: **UIはガラッと変えてよい**。現行レイアウトの踏襲義務はない。
守るべき不変条件は**機能とデータ互換性のみ**。

**最大の変更: 別ウィンドウを廃止し、単一ウィンドウ3ペイン構成にする。**
これで D1・D2・D3・D4・D6 が構造的に消える。

### 4.2 画面構成

```
┌───────────────────────────────────────────────────────────────────────┐
│  GraphMaker    [TGA ▾]   📁 …\demo_data\TGA\raw_data      [Excelへ出力] │ アプリバー
├────────────┬────────────────────────────────────────┬─────────────────┤
│ データ  ⟨  │ X 25 – 800   Y 0 – 105  [自動範囲]      │ 系列│処理│解析│結果│
│            │ 読取X [      ]                          ├─────────────────┤
│ ▸ フォルダ  │ ┌────────────────────────────────────┐ │                 │
│   ツリー    │ │                                    │ │                 │
│            │ │                                    │ │   インスペクタ    │
│ ▸ CSV一覧   │ │        グラフ（常時表示・主役）      │ │                 │
│   □ file_01│ │                                    │ │                 │
│   □ file_02│ │                                    │ │                 │
│            │ └────────────────────────────────────┘ │                 │
│ [グラフへ追加]│                                        │                 │
├────────────┴────────────────────────────────────────┴─────────────────┤
│ ● 8件を追加しました                                    [読込設定] [⚠2] │ ステータスバー
└───────────────────────────────────────────────────────────────────────┘
     280–320px             可変（最大）                    360px
     折りたたみ可                                          折りたたみ可
```

**要件:**
- 左レール・右インスペクタは**折りたたみ可能**。折りたたむとグラフが全幅になる
- グラフは**どの操作中も絶対に隠れない**
- 右インスペクタのタブ: **系列 / 処理 / 解析 / 結果**
  - **`結果` タブは全モードで必ずそのモードの結果を表示する**（D5の解消）
  - 解析を持たないモード（UV-Vis, GPC）では `処理`・`解析` タブを**表示しない**（空タブを出さない）
- 軸範囲・読取X は**グラフ直上のツールバー1箇所のみ**（D6の解消）
- 系列テーブルは**インスペクタ`系列`タブの1つだけ**（D4の解消）。1つの表に:
  表示トグル / 色スウォッチ（クリックで変更）/ 凡例名（ダブルクリックでインライン編集）/ 元ファイル

### 4.3 通知設計 —— `messagebox` を捨てる（D7の解消）

3層に整理する:

1. **インライン検証**: 入力欄の直下に赤系の小さな説明文。フォーカスアウトで判定
2. **ステータスバー**: 成功・進捗・情報（`8件を追加しました`）。数秒で自然に薄くなる
3. **`messagebox` を残すのは2種類だけ**
   - 破壊的操作の確認（系列削除、プロファイル削除）
   - OSレベルの失敗（ファイル書き込み不可等）

「フォルダを選択してください」の類は**ボタンを disabled にし、理由をツールチップ／ステータスに出す**方式へ置換する。

### 4.4 デザイントークン（`theme.py` に一元化。ここ以外に色を書かない）

C2・C3を防ぐため、**16進カラーリテラルを `theme.py` 以外に書くことを禁止**する。

```python
# 面
BG_APP        = "#F7F8FA"   # アプリ地
BG_PANEL      = "#FFFFFF"   # カード・パネル
BG_SUBTLE     = "#F1F3F6"   # 入力欄の窪み・表ヘッダー
BG_HOVER      = "#EDEFF3"

# 罫線（細く・薄く。枠の入れ子をやめる）
BORDER        = "#E3E7ED"
BORDER_STRONG = "#CBD2DB"

# 文字
TEXT          = "#10151B"
TEXT_SECOND   = "#5A6672"
TEXT_MUTED    = "#8A94A0"
TEXT_ON_ACCENT= "#FFFFFF"

# アクセント（現行 #2563A6 は彩度が低く古い。明るく現代的な青へ）
ACCENT        = "#2F6FED"
ACCENT_HOVER  = "#2158CC"
ACCENT_PRESS  = "#1A47A8"
ACCENT_TINT   = "#EDF3FF"   # 選択行・淡い強調

# 意味色
SUCCESS = "#12855A";  SUCCESS_TINT = "#E7F6EF"
WARNING = "#B45309";  WARNING_TINT = "#FEF3E2"
DANGER  = "#CE3B30";  DANGER_TINT  = "#FDEDEB"
DISABLED= "#AEB6C0"
```

**グラフ系列パレット（C1の解消・最重要）**

matplotlib tab10 を捨て、**色覚多様性に配慮した Okabe–Ito 系**を採用する。
科学出版で標準的に推奨されており、白背景・モノクロ印刷でも識別しやすい。
研究者に対する説得力が tab10 とは根本的に違う。

```python
SERIES_PALETTE = (
    "#0072B2",  # 青
    "#D55E00",  # 朱
    "#009E73",  # 緑
    "#CC79A7",  # 桃紫
    "#E69F00",  # 橙
    "#56B4E9",  # 空
    "#6E4B9E",  # 紫
    "#8C6D1F",  # 金茶
    "#47606E",  # 青灰
    "#262626",  # 墨
)
```
※Okabe–Ito の黄 `#F0E442` は白背景で視認性が低いため除外し、`#8C6D1F` 等で置換している。

`model.py` の `DEFAULT_PALETTE` を書き換えるのではなく、**新パレットを `theme.py` に定義し、
系列追加時に UI 側から色を割り当てる**（`model.py` は移植のまま触らない）。

**タイポグラフィ（C6の解消）**

日本語環境で最も自然に出るのは `Yu Gothic UI`。和欧混植も破綻しにくい。

```python
FONT_FAMILY   = "Yu Gothic UI"   # fallback: "Meiryo UI" → "Segoe UI"
FONT_MONO     = "Consolas"       # 数値列は等幅で桁を揃える（科学UIでは効果大）

FONT_TITLE    = (FONT_FAMILY, 16, "bold")   # アプリ名
FONT_HEAD     = (FONT_FAMILY, 11, "bold")   # セクション見出し
FONT_BODY     = (FONT_FAMILY, 10)           # 通常
FONT_LABEL    = (FONT_FAMILY, 9)            # 補助・単位
FONT_NUM      = (FONT_MONO,   10)           # 解析結果の数値
```
起動時に利用可能フォントを検査し、無ければ順にフォールバックすること。

**スペーシング（8ptグリッド）**: `4 / 8 / 12 / 16 / 24 / 32`
D8・D9 の密集を防ぐため、**入力群は1行に最大4要素まで**。超える場合は折り返すか小見出しで分割する。

**枠の扱い（C5の解消）**: `LabelFrame` の入れ子を禁止。
グルーピングは **「小見出しテキスト＋余白」** で行い、罫線は最小限にする。

### 4.5 高DPI対応（C7の解消）

起動時（`Tk()` 生成前）に実行する:

```python
import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)   # Win8.1+
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()     # Win7 fallback
    except Exception:
        pass
```
加えて `root.tk.call("tk", "scaling", <dpi>/72.0)` でスケーリングを合わせる。
グラフ描画も論理px前提で固定値を持たず、スケール係数を掛けること。

### 4.6 全Toplevelの背景（C4の解消）

`Toplevel` を作る箇所すべてで `self.configure(background=theme.BG_APP)` を必ず呼ぶ。
ダイアログ基底クラス `BaseDialog` を作り、背景設定・モーダル化・中央寄せ・
Esc/Enter バインドを共通化すること。

---

## 5. 機能要件

**機能は現行と完全に同等**であること。仕様の詳細は元リポジトリの
`README.md` に日本語で網羅されているので、**必ず最初に全文を読むこと**:

```
C:\Users\giggl\OneDrive\ドキュメント\仕事効率化\GraphMaker_Python39\README.md
```

要点のみ再掲:

- **9モード**それぞれで 読込 → 比較表示 → （処理）→（解析）→ Excel出力
- **TGA**: Td5/Td50/Td95 一覧、任意残存率から Tdx を線形補間
- **DSC**: ブランク補正、Tg解析（前後ベースライン・オンセット・中点・変曲点）、
  融解解析（オンセット・ピーク・終了・ΔH）、**グラフ上4点選択**、採用/除外
- **IR**: ブランク補正 → 0点合わせ → 規格化（この順序厳守）、横軸は高波数を左（`reverse_x`）
- **UV-Vis / GPC**: 生データ比較表示とExcel出力のみ（解析なし）
- **粒度分布**: 底10対数X軸（`logarithmic_x`）、指定粒径での規格化
- **温度ロガー**: 4点でベースライン回帰、ピーク開始/トップ/終了、ピーク面積
- **SSカーブ**: 幅・厚み・L0 から Strain/Stress、選択2点範囲の最大応力、ヤング率回帰
- **粘着力**: 試料幅から N/25mm 換算、選択距離範囲の距離加重平均
- **読取X**: 全9モード共通。数値入力またはグラフクリックで補間Y値を系列表に表示
- **読込プロファイル**: ヘッダー自動検索・行番号・キーワード相対位置・列名/列番号・
  文字コード・区切り文字・単位・6種類の終了条件。組み込みは上書き不可
- **モード別状態分離**とルートフォルダのモード別保存

---

## 6. 実装フェーズと検証ゲート

**各フェーズ終了時に必ず検証し、通らなければ次へ進まない。**

### Phase 0 — 土台とロジック移植 ★最重要チェックポイント

1. `C:\Users\giggl\claude_project\graph` にプロジェクト骨格を作る
   （`src/graphmaker/`, `tests/`, `profiles/`, `demo_data/`, `pyproject.toml`, `requirements.txt`）
2. §2.3 の**純ロジック22ファイルをそのままコピー**
3. `tests/` 33ファイル、`profiles/`、`demo_data/` をコピー
4. パッケージ名を `tga_analyzer` → `graphmaker` に変更する場合は、
   **テストのimport行を機械的に置換するだけ**にとどめる（ロジック本体は触らない）
5. UIモジュールに依存するテスト（`test_ui_redesign.py`, `test_graph_window.py`,
   `test_readout_ui_integration.py` 等）は**この時点では一旦保留リストに退避**してよい。
   ただしPhase 6で**新UIに合わせて必ず復活させる**

**🚦ゲート: 純ロジックのテストが全て成功すること。**
```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
python -m unittest discover -s tests -v
```
（元は224テスト全成功。UI依存分を除いた数が全て通ること）

### Phase 1 — デザインシステム
`theme.py` に §4.4 のトークン、ttkスタイル定義、フォントフォールバック検査、
§4.5 のDPI対応を実装。ttk の `clam` テーマをベースに、
`TFrame/TLabel/TButton/TEntry/TCombobox/Treeview/TNotebook/TPanedwindow/
 TCheckbutton/TRadiobutton/TSpinbox/TScrollbar` の**全てにスタイルを当てる**
（当て漏れが「素のグレー」の原因になる）。

### Phase 2 — グラフ描画エンジン `plot_view.py`
`DisplaySeries` のリストを受けて tkinter Canvas に描画する。

- 入力は必ず `DisplaySeries`。モード分岐を描画側に持ち込まない
- 対応: 通常軸 / **反転X（IR）** / **対数X（粒度分布）**
- 目盛は元の `nice_ticks()` / `logarithmic_ticks()` / `format_tick()` の考え方を踏襲
- 薄いグリッド（`BORDER`）、黒い軸線、内向き目盛、余白は8ptグリッド準拠
- 凡例、系列の表示/非表示、線幅、ホバー時の強調
- 解析用オーバーレイ（縦破線・バンド・マーカー・ベースライン直線・積分面積）
- **キャンバス座標 ⇄ データ座標の相互変換**（クリック解析の基盤）
- リサイズ追従、高DPIスケール対応

**🚦ゲート: 9モードすべてでデモデータを描画し、スクリーンショットで目視確認。**

### Phase 3 — メインウィンドウ骨格
§4.2 の3ペイン単一ウィンドウ。モード切替、フォルダツリー、CSV一覧、
グラフへ追加、系列テーブル（表示/色/凡例名インライン編集）、
ステータスバー、§4.3 の通知機構、左右ペインの折りたたみ。

### Phase 4 — モード別の処理・解析パネル
インスペクタの `処理` / `解析` / `結果` タブ。
9モード分の設定UIと、**グラフ上クリックによる点選択**
（`dsc_selection.py` / `generic_selection.py` の状態機械をそのまま使う）。
`結果` タブは全モードで機能すること（D5）。

### Phase 5 — ダイアログ群
`BaseDialog` 基底クラス（§4.6）の上に、
読込設定ダイアログ（`import_profiles.py` のロジックを使う）、
試料情報ダイアログ、色一括変更、凡例名一括編集を再実装。

### Phase 6 — Excel出力とテスト復活
`excel_export.py` を新UIから呼ぶ。Phase 0で退避したUI依存テストを
**新UI構造に合わせて書き直して復活**させる。

**🚦ゲート: 全テスト成功 + 実際にExcelを出力し、§1.3の書式を目視確認。**

### Phase 7 — 仕上げ
`GraphMaker.pyw` / `GraphMaker.bat` ランチャー、`README.md`、
Python 3.9 互換テスト（元 `test_python39_compatibility.py` を活用）、
9モード通し操作の手動確認。

---

## 7. 検証方法

### 7.1 自動テスト
```powershell
cd C:\Users\giggl\claude_project\graph
$env:PYTHONPATH=(Resolve-Path 'src').Path
python -m unittest discover -s tests -v
```

### 7.2 目視確認（UI作業では必須）

**UIを変更したら必ずスクリーンショットを撮って自分の目で確認すること。**
以下の手順が実際に動作することを確認済み:

```powershell
# 1. 起動
$env:PYTHONPATH = "C:\Users\giggl\claude_project\graph\src"
Start-Process python -ArgumentList "-m","graphmaker" -PassThru

# 2. ウィンドウ位置の取得（EnumWindows でタイトル一致を探す）
# 3. 指定領域をキャプチャ
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size $w, $h))
$bmp.Save("$env:TEMP\shot.png", [System.Drawing.Imaging.ImageFormat]::Png)
```
撮った PNG を Read ツールで開けば画像として確認できる。
マウス操作は `mouse_event` の P/Invoke で自動化できる
（`Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing` が必要）。

デモデータは `demo_data/<モード>/raw_data/` に9モード分揃っている。

### 7.3 受け入れ基準

- [ ] 全テスト成功
- [ ] 9モードすべてで 読込→表示→（解析）→Excel出力 が通る
- [ ] Excel出力が §1.3 の書式を満たす
- [ ] §3 の問題 D1–D9・C1–C7 が**すべて解消**している
- [ ] 色リテラルが `theme.py` 以外に存在しない（grep で確認）
- [ ] `messagebox` の使用が破壊的確認とOSエラーのみ（grep で確認）
- [ ] 別ウィンドウなしで DSC の Tg 解析が完結する
- [ ] Python 3.9 互換（3.10+構文を使っていない）
- [ ] 外部依存が `openpyxl` のみ

---

## 8. 作業の進め方

1. **まず元の `README.md` を全文読む**（機能仕様の正典）
2. Phase 0 を完了させ、**テストが緑になるまで他のことをしない**
3. 各フェーズのゲートを守る
4. UI実装中は**こまめにスクリーンショットで自己確認**する
5. 判断に迷う仕様は、元コードの該当箇所を読んで挙動を確認する
6. 元ディレクトリには**絶対に書き込まない**

### やってはいけないこと

- ❌ 純ロジック22ファイルの中身を「ついでに改善」する
- ❌ Excel出力書式を変更する
- ❌ numpy / pandas / matplotlib を導入する
- ❌ Python 3.10+ 構文を使う
- ❌ 動作確認せずに「完成しました」と報告する
- ❌ 元ディレクトリを変更する
