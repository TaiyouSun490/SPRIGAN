# 3Dモデル検索・採用判断・ダウンロード

FAFNIRの既存UI上部の **3D MODELS** を開くと、Sketchfab／Poly Havenを検索し、候補の確認根拠と採用・保留・却下の判断を保存できます。検索は所有権や利用許可を意味しません。配布元のライセンスと作者を各候補に保持します。

リファレンスとの照合、Jevの判定範囲、Sketchfabの接続保存は [Reference-based model review](REFERENCE_REVIEW.md) を参照してください。

## 起動と通常の操作

FAFNIRをこのソースからインストールし、起動します。Python 3.12以上が必要です。

```powershell
python -m pip install -e .
fafnir ui
```

既定の画面は `http://127.0.0.1:8770/models.html` です。

1. 検索語と検索元を選ぶ。配布元の言語に合わせた英語の語句が有効です。
2. 候補の画像や「配布元で3D・ライセンスを確認」を開いて確認し、比較対象を選ぶ。
3. 対象プロジェクト、用途・見た目の目標、必要なら負荷・ライセンス条件を入力する。
4. 見た目の確認結果と根拠を書き、「選択した候補を評価」を押す。
5. 未確認項目を確認して、理由付きで採用・保留・却下を記録する。条件や根拠を変更したら再評価する。
6. 形式とテクスチャ解像度を選び「取得内容を確認」へ進む。ライセンス、作者、保存先、容量上限を確認して取得する。

Poly Havenの公開APIが返さない三角形数・材質数は未確認のままです。Sketchfabの三角形数も配布元情報であり、Unityでの実測ではありません。材質数・負荷の自動実測やUnityへの自動Importは今回の機能に含みません。

「条件確認＋手動判断」はAIを呼びません。条件外は却下、その他は保留として提示し、利用者が実際の確認根拠に基づく最終判断を保存します。条件外・未確認がある場合は、Jevが高い確信度で採用を返しても採用推奨に変えません。明示的な手動判断は別の履歴として記録できます。

## データとグラフ

既存カタログと同じSQLiteファイルに、独立した `model_nodes`、`model_edges`、`model_operations` テーブルを追加します。モデル、作者、配布元、タグ、形式、ライセンス、プロジェクト、評価、判断、取得記録をノードと型付きエッジで保存します。Neo4j等の外部サービスは不要です。

- 同じモデルの再検索・詳細更新で判断履歴は消えません。
- 評価は入力ハッシュ、モデル情報のリビジョン、条件、確認根拠、評価器・モデル名を保持します。
- 判断後にモデル情報が変わると、旧評価による新しい判断を拒否します。
- グラフ取得は深さ1〜3、最大150ノード／エッジで制限し、切り詰め時は `truncated` を返します。
- Web UI、CLI、MCPで同じDBを指定すれば履歴を共有します。OAuthのメモリ上のセッションは共有しません。Windowsで接続の暗号化保存を選んだ場合は、同じカタログを使う別FAFNIRプロセスも保存済み接続を再読み込みできます。

## 取得形式と保存先

Sketchfabは公式APIのglTF ZIP／GLB／USDZ、Poly Havenは配布されているglTF／FBX／Blender／USD等に対応します。実際の形式・解像度はモデルに依存します。Sketchfabの解像度はパッケージ側で決まり、FAFNIRの解像度指定は使われません。

Poly Havenは本体とAPIの `include` に列挙された相対パスのテクスチャ・バイナリを一緒に取得します。ZIPはパスと展開容量を検査してそのまま保存し、自動展開・スクリプト実行しません。

保存先はDBディレクトリ下の `model-downloads/<取得ID>/`。`FAFNIR_MODEL_CACHE` で変更できます。取得上限は既定512 MiBで、UIの「接続・保存先」またはAPIの `max_bytes` から変更できます。新しいディレクトリへ一時取得し、成功後に公開します。上書きはしません。失敗は履歴に残し、新しい計画でやり直します。

各取得には作者、モデルURL、ライセンスURL、ファイルサイズ、SHA-256を記した `attribution.json` が付きます。期限付きの取得URLや認証トークンはDB・レシートへ保存しません。同じ完了済み計画の再実行は同じレシートを返します。

## Sketchfabの認証

公開検索・詳細は認証なしで利用できます。ダウンロードには公式OAuthが必要です。

ブラウザーUI用にSketchfabでOAuthアプリを登録し、起動するプロセスに次を設定します。

- `FAFNIR_SKETCHFAB_CLIENT_ID`
- `FAFNIR_SKETCHFAB_CLIENT_SECRET`
- `FAFNIR_SKETCHFAB_REDIRECT_URI=http://127.0.0.1:8770/api/models/oauth_callback`（実際に使用するポートと一致させる）

画面の「接続・保存先 → Sketchfabにログイン」から公式認証へ進みます。stateの期限は10分、一回限りです。標準ではアクセストークンと更新トークンをサーバープロセスのメモリに保持し、終了すると消えます。Windowsでは接続設定画面の暗号化保存を選べます。保存先・解除・共有範囲は [接続設定の説明](REFERENCE_REVIEW.md#sketchfab-connection) を参照してください。

CLIや独立したstdio MCPプロセスには、公式OAuthで取得した利用者のトークンを `FAFNIR_SKETCHFAB_ACCESS_TOKEN` として渡せます。UIだけのメモリ上のログインは別プロセスへ伝播しません。WindowsでFAFNIR自身の接続を保存した場合は、その保存済み接続を利用できます。ブラウザーや他のアプリの認証情報を読み出す機能はありません。

設定名の一覧は [`.env.models.example`](../.env.models.example)。説明用ファイルであり、自動読み込みはしません。実際の値はプロセスの環境変数として設定し、リポジトリやチャットへ貼り付けないでください。

## JevとAnyJev

### TypeSafe Jev

`TYPESAFE_API_KEY` をFAFNIRの起動環境へ設定し、評価方法でJevを選びます。公式 `https://api.typesafe.ai/v1/systemone` を使用し、モデル情報、用途、条件、確認根拠を送ります。デフォルトの `jev-latest` は `FAFNIR_JEV_MODEL` で変更できます。画像本体、Unityプロジェクト本体、認証情報は評価データに含めません。

回答はadopt／hold／rejectと分布・確信度を検証し、モデル名・使用量とともに保存します。Jevが返さない自由文の理由を生成したことにはしません。根拠は確認済みチェックと利用者が記録した理由です。

### AnyJev

```powershell
python -m pip install -e '.[decision-local]'
```

利用者が用意したローカルvLLMへ接続します。`FAFNIR_ANYJEV_MODEL` と必要なら `FAFNIR_ANYJEV_URL`（既定 `http://127.0.0.1:8000`）を設定します。loopback以外の接続先は受け付けません。FAFNIRは推論モデルの自動取得やGPUサーバー起動をしません。

AnyJevの勝ち選択肢の確率は未校正です。Jevの確信度と同一視せず、提案値は参考として残し、最終判断の提示は保留にします。

## Dots・MCP

9つのモデル用ツールを既存FAFNIR MCPに追加しています。

| 用途 | ツール |
|---|---|
| 接続状態 | `get_3d_model_status` |
| 検索・詳細 | `search_3d_models`, `get_3d_model_details` |
| 関係と履歴 | `get_3d_model_graph` |
| 評価・判断保存 | `evaluate_3d_model`, `record_3d_model_decision` |
| 取得計画・実行・状態 | `prepare_3d_model_download`, `download_3d_model`, `get_3d_model_download_status` |

[ローカルプラグイン](../plugins/fafnir-models/SETUP.md) にstdio設定と採用判断のスキルを同梱しています。Dots専用の非公開APIを仮定せず、利用者が認可したローカルツール接続を使います。クラウドDotsから使う場合は、対応するホスト機能でローカルコンピューターへ接続し、そのコンピューターとホストアプリを起動しておく必要があります。プラグインはアカウントへのインストール・公開を自動では行いません。

## CLI

```powershell
fafnir --json models status
fafnir --json models search --request model-search.json
```

`model-search.json` の例：

```json
{"query":"rusty pipe","source":"all","limit":12}
```

`models` のactionは `status/search/detail/graph/evaluate/decide/prepare_download/download/download_status`。要求はUTF-8のJSONファイルで渡します。具体的な入力フィールドはMCPのツールスキーマでも確認できます。

## 実装検証

自動テスト：

```powershell
python -m pytest -q tests/test_model_discovery.py tests/test_model_integrations.py
```

明示的に外部APIと小さなモデルの実取得を検証する場合：

```powershell
python tests/model_live_smoke.py
```

このスクリプトは一時DB／一時キャッシュで実行し、終了時に自身の検証データを削除します。通常のpytestでは実行しません。

2026-10-04の検証では、両配布元の実検索、Sketchfab詳細、Poly Haven glTF 1kと関連4ファイルの実取得・ハッシュ確認を実施。ブラウザーでは選択、評価、保留保存、関係履歴、取得内容表示、キャンセル、条件変更後の再評価案内を確認しました。

Sketchfabの実アカウントログイン・認証付き取得、Jevの有料API推論、AnyJevの実バックエンド推論、Dotsのアカウント接続は未検証です。これらは利用者の設定が必要です。通常の所有Unityアセット用RAGは別機能で、今回の軽量検証環境にはtorch／transformersを導入していません。

公式仕様：[Sketchfab Download API](https://sketchfab.com/developers/download-api/downloading-models)、[Sketchfab OAuth](https://sketchfab.com/developers/oauth)、[Poly Haven API](https://github.com/Poly-Haven/Public-API)、[TypeSafe API](https://docs.typesafe.ai/api)、[AnyJev](https://github.com/nokia-applied-research/AnyJev)、[OpenAI Dots](https://learn.chatgpt.com/docs/dots)。
