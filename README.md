# grad-insight – 抽象方針に基づく汎用抽出基盤

本プロジェクトは、大学院の教員情報を Google Sheets を起点に網羅・更新するための抽出基盤です。個別対症ではなく、設定優先＋汎用ヒューリスティクスの自動補完により、構造多様性へ段階対応します。

## 変更点（このPR）
- 層別方針の強化（要約）
  - ページ分類: `auto`（table/cards/list）自動判定を追加（未指定時に使用）。
  - 人物URL判定: `config/adapters.json` にホスト別の正規表現・除外ヒントを追加し、`looks_individual_link` に統合。
  - 非人物ドロップ: タイトル語やアダプタのキーワードで非人物を緩く判定して除外（bulk系）。
  - マージ安全化: キー優先度は `link(個人URL)` > `name+lab` > `name` > `frag/anon` を維持・可視化。
  - 観測性: Examples 経路では per-target JSON（counts/merge/drops/limits/code）とサンプルCSVを `logs/` に出力。
- 設定ファイルの追加
  - `config/adapters.json`: ホスト別の知見（個人URLの正規表現、カテゴリ除外、主要キーワード）。デフォルトとホスト固有をマージ。

## 主要ファイル
- `src/run_extract.py`
  - DOM列挙+OCR+CSS の多段抽出（Examples 経路）
  - `auto` ページ分類（未指定時に table/cards/list を判定）
  - 個人URL判定・非人物ドロップ・行マージ・構造化ログ出力
- `config/adapters.json`
  - ホスト別アダプタ。`default` とホスト名の両方をマージして適用。
- `scripts/examples_to_targets_json.py`
  - Google Sheets の `examples` タブからターターゲットJSONを生成（Secrets: `SHEET_ID`, `GOOGLE_CREDENTIALS_JSON`）。
- `.github/workflows/examples.yml`
  - CI 実行（ヘッドレスChromium＋OCR）。Artifacts に CSV/ログ/証跡を保存。

## 実行
1) 依存導入とブラウザ
```
python3 -m pip install -r requirements.txt
python3 -m playwright install --with-deps chromium
```

2) Examples → ターゲット生成（CIと同じ）
```
export SHEET_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
export GOOGLE_CREDENTIALS_JSON='{"type":"service_account",...}'
python3 scripts/examples_to_targets_json.py
```

3) 抽出（Examples生成物を使う場合）
```
# Bulk 運用推奨（固定値は補完のみ、0件や非人物は安全側でドロップ）
export EXAMPLES_MODE=bulk
export EX_ENUM_MAX_ITEMS=300
export EX_ENUM_MAX_SHOTS=8
export EX_ENUM_TIMEOUT_MS=240000
export EX_NAV_TIMEOUT_MS=15000
export EX_ACTION_TIMEOUT_MS=5000
export DEBUG_ARTIFACTS=1
export DEBUG_SAMPLE_N=80

python3 -u -m src.run_extract config/examples_targets.json
ls -1 *.csv logs/ evidence/
```

4) 単体ターゲット（`config/targets.json` など）
```
python3 -u -m src.run_extract config/targets.json
```

## Examples → HTMLブロック出力（新機能）
- 概要: Examplesシートから各URLのHTMLを取得し、本文寄りDOMから「ブロック」を抽出。同一ブックの新タブに書き出します（OCRは未使用）。
- 使い方:
```
export SHEET_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
export GOOGLE_CREDENTIALS_JSON='{"type":"service_account",...}'
python scripts/sheet_blocks_from_examples.py --sheet-id $SHEET_ID --examples-name "examples" --max-blocks 300
```
- 出力タブ: 「大学名-研究科-blocks」。既存と衝突した場合は末尾に -2, -3 を付与。
- 出力列（ヘッダー固定）: `run_id, university, graduate_school, source_url, page_id, block_id, tag, depth, group_id, path, has_img, text, links_json`
- ブロック化要点:
  - 除去: `script/style/noscript/svg/canvas/nav/aside/footer/header`
  - 対象タグ: `div,section,article,li,td`（最大300件）
  - 相対URLは絶対化（a/@href, img/@src）
  - 文字ありノードを優先し、同型反復は代表から詰めて飽和防止

## 観測性（KPI/ログ）
- `logs/<target>.json`
  - `meta`: run_id, host, mode, capabilities(playwright/ocr)
  - `counts`: dom_items, rows_out, unique_rows, fetched_items_css/fb, before_rows
  - `merge_keys`: link/name+lab/name/frag/anon の件数
  - `drops`: no_name_link, not_person, dup_key
  - `limits`: EX_* 環境変数（上限/タイムアウト）
  - `status/code`: OK / WARN / ERROR と DOM_ENUM_ZERO / EXTRACT_ZERO / MERGE_COLLAPSE
- 証跡: `evidence/` に HTML（original/mark強調）と要素スクリーンショット

## アダプタ（拡張手順）
- `config/adapters.json` にホスト名キーを追加し、以下を必要最小で指定してください。
  - `personal_url_patterns`: 個人ページらしいパスの正規表現
  - `generic_list_hints`: 一覧/カテゴリのパス（個人URLキーに使わない）
  - `exclude_url_substrings`: 除外パス断片
  - `exclude_text_keywords`: 非人物（ニュース/カテゴリ等）を示す文言

## テスト結果（ローカル抜粋）
- `config/targets.json` の慶應商学研究科にて 1 行抽出、CSV 生成を確認
- Examples 経路は Secrets 未設定のためスキップ（CI では Sheets 接続可）

## 今後（中期強化の入口）
- アダプタの拡充（上位 10–20 ホストを起点）
- CSS探索順序の最適化と日本語姓名の補助規則（姓だけ抽出の低減）
- 0件・潰れ・混入の即時判別（ダッシュボードの小型スクリプト化）

---
非互換な仕様変更はありません。既存の Examples/CI 運用を維持しつつ、未指定ページでも `auto` 判定とアダプタ知見で回収率と安定性を高めます。
