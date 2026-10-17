# Asset Hub — 3D Asset Serverを土台にしたFAFNIR拡張

素材の横断検索・取得に、**所有素材、CC0、音素材、AI生成、接続設定、利用条件、制作向けの構成表**をまとめました。既存のUnity Asset Store連携、モデルの評価・採用判断、RAGは保持します。

公開素材は [3D Asset Server](https://github.com/arielshad/3d-asset-server) のREST APIを利用します。Node/Dockerを新たにインストールする必要はありません。サービスを自己ホストする場合は、起動前に `FAFNIR_ASSET_SERVER_URL` を指定できます。既定値は `https://3d.shep.bot`、自己ホストのHTTPはloopbackのみです。第三者サービスの稼働と各提供元の仕様に依存します。

## 入口

通常の `fafnir ui` を起動し、上部の **ASSET HUB** を開きます。既定URLは `http://127.0.0.1:8770/hub.html` です。

1. **素材を探す**：所有素材を優先し、CC0の公開素材も検索。種類・提供元・利用根拠で絞り込み。
2. **所有素材を同期**：BOOTH・Fab・Humbleの全件同期、拡張機能からの登録、JSON/CSV取込、素材フォルダーの関連付け。
3. **AIで生成**：内容を準備し、料金確認後に一度だけ送信。進行状況・出力取得・履歴を管理。
4. **接続設定**：Sketchfab OAuthと生成サービスのAPIキーを一か所で設定。

コード変更を既存のMCPプロセスへ反映するには、そのFAFNIR接続を再起動してください。稼働中の他プロジェクトやEditorは自動で再起動しません。

## 対応範囲

| 分類 | 提供元 | 実装した接続方法 |
| --- | --- | --- |
| 公開素材 | 3D Asset Serverが返す提供元一覧。Poly Haven、ambientCG、Kenneyなど | 公開検索、形式・解像度選択、容量制限付き取得、関連ファイルの保持、出典・チェックサム |
| Unity Asset Store | 既存の所有カタログ | Asset Hub検索から参照。取得・My Assets同期は既存の認証済みEditorブリッジを継続利用 |
| BOOTH、Fab / Unreal Engine Marketplace、Humble | 購入素材 | ブラウザーの購入ライブラリを全件走査。ページ送り・バンドル内アイテム・再開・重複防止・定期更新。所有メタデータを取り込み、ファイル取得は分離 |
| Gumroad、itch.io | 購入素材 | 商品単位の登録、表示中ライブラリのリンク読込・選択・同期、JSON/CSV取込 |
| ArtStation、CGTrader、TurboSquid、Superhive / Blender Market、BlenderKit、Fanatical、GameDev Market | 購入素材 | 商品ページから自己申告登録、FAFNIR形式の一覧取込、ダウンロード済みファイル関連付け |
| Sonniss、A Sound Effect、AudioJungle / Envato、Pond5 | SE・BGMなどの購入素材 | 同上。音源はライブラリ内で試聴可能 |
| Sketchfab | 公開検索・モデル取得・所有素材 | 既存OAuth取得を維持。統合した設定案内・ログイン・解除・暗号化保存・別FAFNIRプロセスでの保存接続再読込 |
| Tripo | 3Dモデル | 公式APIのtext-to-model、タスク照会、生成物取得。モデル指定などは詳細オプション |
| ElevenLabs | SE・BGM・音声 | 公式APIのsound-generation / music / text-to-speech。長さ・ループ・歌声なし・Voice IDを画面から指定 |
| World Labs Marble | ワールド | 公式v1 APIのテキスト、画像URL、動画URL指定、操作照会、出力取得。画面はテキストと画像URL、動画URLはAPIの詳細オプション |
| Suno | 自分の生成音源 | 書き出した音源と契約・利用条件を登録。公式公開生成APIの仕様は確認できておらず、架空の直接APIやCookie抽出は実装しない |

BOOTH・Fab・Humbleの全件同期は、公式サイトにログインしたブラウザー拡張から行います。公開OAuth APIによるサーバー同期ではありません。サイト内部のライブラリ形式に依存するため、ログイン切れ・アクセス制限・形式変更は未完了として停止します。他の購入サイトはエクスポート・登録・公式ダウンロードを利用します。Fabの`.uasset`をUnityで使えるとは扱いません。

## 購入素材を取り込む

ブラウザー拡張は `browser_extension/unity_asset_store/` の **0.4.0** です。既存のUnity機能を保持しています。更新後はChrome/Edgeの拡張管理画面で再読み込みしてください。初回導入・Native Host設定は [拡張機能README](../browser_extension/unity_asset_store/README.md) を参照してください。

### BOOTH・Fab・Humbleの全件自動同期

1. 拡張の **BOOTH・Fab・Humbleを全件同期**、または拡張のオプションを開く。
2. 各カードの **接続・ログイン** を押す。初回はそのサイトだけへのアクセスをブラウザーで許可し、開いた公式ライブラリでログインする。パスワード・CookieをFAFNIRへコピーする必要はない。OAuthアプリの作成も不要。
3. 同期するサイトを選び、所有の確認欄をチェックする。自動更新は今回だけ／6時間／1日／1週間から選ぶ。
4. **選択したサイトをまとめて全件同期** を押す。ポップアップや専用画面を閉じても、ブラウザー起動中は続行する。
5. 進捗は各カード、Asset Hubの「所有素材を同期」、MCP `get_owned_library_sync_status` で確認する。

| 提供元 | 走査対象・終了条件 |
| --- | --- |
| BOOTH | `accounts.booth.pm/library` と `/library/gifts` の購入・受取ギフトカードを全ページ走査。両方のページ送りが終了したとき完了 |
| Fab | `/i/library/search?sort_by=-createdAt&source=acquired` の取得済みライブラリをカーソル終端まで走査。画面の無限スクロールや表示件数には依存しない。非公開化された項目も安定した商品IDで保持 |
| Humble | `/api/v1/user/order` の全注文を列挙し、各注文の `subproducts` と、返された `tpkd_dict.all_tpks` の商品名・商品IDを取込。バンドル内アイテムを展開し、同じ商品の重複を除外。キーの表示・引き換え・購入は行わない |

サイト別の **一時停止・再開・最初から** を利用できます。通信断や429は時間を置き最大3回再試行し、その後は未完了で停止します。ログイン待ちのサイトだけ停止するので、他サイトは続行します。定期同期は完了したサイトを新規走査し、一時停止・認証待ち・確認待ちは勝手に再開しません。ブラウザー終了中は実行せず、次回起動時に期限と進捗を読み直します。

既定上限は1サイト10000ページ／バッチです。詳細設定から変更でき、上限到達は成功扱いにしません。1回の取込は最大200商品。Humbleの巨大バンドルも分割します。全件取得とはライブラリの**メタデータ**の取得で、素材本体の大量ダウンロード・展開・Unity取込は行いません。

チェックポイントと未送信ページは `chrome.storage.local`、受領済みページ・商品ID・完了証跡はFAFNIRのSQLiteへ保存します。Native Hostへの送信直前にページを保存し、応答喪失時も同じ内容を再送して重複計上を防ぎます。再同期は既存の利用条件確認・メモ・タグ・種類・ローカルファイル関連付けを保持し、今回見つからなかった既存素材も削除しません。購入品の失効・返金の自動判定はしません。

ログイン情報・注文キー・署名付きダウンロードURL・APIの生レスポンスはFAFNIRへ送りません。Humbleの注文ハンドルはブラウザー内の一時メモリだけで使い、保存するのは順番と一覧のハッシュです。Fabのページカーソルもブラウザー内に留めます。ページ途中のアカウント切替・購入一覧変更を検出した場合は最初から走査します。BOOTH/Fabの検出は先頭ページの商品IDのハッシュであり、アカウントを独立に本人確認する仕組みではありません。同期中は同じアカウントを利用してください。

`browser_full_library` は実装方式を示す値であり、実アカウントでの動作保証や第三者による購入証明ではありません。常に `purchase_verified: false` を残します。実サイトの認証済みアカウントでの全件確認はまだ行っていません。

### 商品・一覧ファイルからの取込

- 商品ページ：**この商品を登録候補にする** → 選択 → 所有を申告 → **選択した所有素材を同期**。
- **表示中の購入ライブラリを読込** は現在ページだけの補助機能です。3サイトの全件取得には上記の専用画面を使用してください。
- 公開商品URLがないHumbleの商品・非公開化されたFab項目は、全件同期では `source_product_id` で保持し、存在しない商品URLを生成しません。公開URL前提のJSON/CSV取込とは区別します。

JSON/CSVは任意の購入履歴を自動判別するものではなく、以下の共通形式です。画面からCSVひな形を保存できます。

必要項目は `title` と `url`。任意項目は `type`, `license`, `license_url`, `author`, `description`, `tags`, `root_id`, `file`, `engine_restriction`, `license_reviewed`, `entitlement_note`。JSONは配列または `{ "items": [...] }`、CSVは同名のヘッダーを使います。`type` は `model/material/texture/hdri/animation/world/audio/sfx/music/voice/pack/script/sprite/ui/font/other`。

`engine_restriction` は `none/unity/unreal/unknown`。不明な条件を「制限なし」に自動変換しません。購入済みという自己申告と、独立した購入確認を区別し、常に `purchase_verified: false` を残します。繰り返しの同期は商品URLに基づいて同じレコードを更新し、ページにない既存の素材を削除しません。

ローカルファイルは、素材専用フォルダーを登録して得た `root_id` と、その中の相対パス `file` で関連付けます。ドライブ全体・ホーム全体の登録、登録外へのパス、ファイルの実行は許可しません。

## 接続を設定する

### Sketchfab

**接続設定 → Sketchfab** に状態と次の操作を表示します。アプリ設定済みなら **Sketchfabに接続** で公式ログインへ進みます。認証後はAsset Hubへ戻ります。

未設定時は初回設定欄が開きます。コールバックURLをコピーし、[Sketchfab公式案内](https://sketchfab.com/developers/oauth#registering-your-app)に従って発行されたClient ID / Client Secretを入力します。通常ログインだけでOAuthアプリ登録を省けるとは案内しません。必要な設定がまだ発行されていないときは、その工程が必要です。

Windowsでは **Windowsで暗号化して接続を保存** を使えます。FAFNIRが管理する接続だけをユーザー単位のDPAPIで保存し、ほかのFAFNIRプロセスのログイン・解除を変更検知して読み直します。他アプリの認証情報は取得しません。秘密情報をAIチャットへ貼る必要はありません。

### 生成サービス

各サービスの公式案内から発行したAPIキーを、このローカル画面に入力します。Windowsでは暗号化保存、ほかのOSでは現在のプロセス内保存が利用できます。statusが返すのは設定の有無で、キーそのものは返しません。**設定済みはAPI認証を実際に試したという意味ではありません。**

環境変数も利用できます：`TRIPO_API_KEY`, `ELEVENLABS_API_KEY`, `WORLDLABS_API_KEY`。UI入力・保存を使えば、MCP設定ファイルやチャットへキーを書く必要はありません。

## ダウンロードと生成の挙動

- 公開検索はCC0を厳密に判別します。無料、CC BY、独自条件付きの「CC0風」をCC0に置き換えません。
- 取得前に現行のCC0条件、形式、解像度、出力先、容量上限を確認します。既定32MiB、APIから変更可能。保存先はFAFNIRの専用キャッシュです。
- ZIPは展開量・件数・パス・シンボリックリンクを検査。データ形式だけを展開し、glTFのbin/texture相対配置を保持します。コード・実行形式は自動展開・実行しません。
- 取得したファイルには出典・利用条件・SHA-256を残します。署名付きCDN URLを永続的な出典やログにしません。Unityなどのプロジェクトへは自動投入しません。
- AI生成は準備と実行を分離します。表示したリクエストとプロバイダー課金への同意が必要です。**出力サイズ上限は料金上限ではありません。** 実料金の見積り・プロバイダー側の価格上限は未実装です。
- 課金リクエストのタイムアウトは `submission_unknown`。自動再送しません。サービス側の履歴を確認してから対応します。
- 生成物は `generated` として保存します。自動でCC0・商用可とは扱わず、契約条件を記録します。

## AI・APIから使う

MCPには `search_assets`, `get_asset`, `download_asset`, `list_providers` の語彙に加え、`sync_owned_asset_manifest`, `register_owned_asset_folder`, `prepare_generated_asset`, `generate_asset`, `poll_generated_asset`, `collect_generated_asset`, `record_asset_usage_rights`, `export_project_asset_manifest` を追加しました。

`download_asset` はユーザーが取得を依頼した素材に対して呼びます。その許可を限定した取得計画に引き継ぎ、同じ内容の承認を繰り返し求めません。生成の課金同意は別です。機密入力用の設定APIはMCPに公開しません。

3D Asset Serverに近いREST入口：

- `GET /v1/providers`
- `GET /v1/search?q=forest&type=hdri&rights=cc0`
- `GET /v1/assets/{id}`、`GET /v1/assets/{id}/files`
- `GET /v1/assets/{id}/download`：CC0の現行条件を再確認し、提供元バンドルへリダイレクト。所有ファイルは限定したローカル取得・公式ストアの手順を使用。
- `POST /api/hub/{action}`：UIと共通の詳細操作。変更操作は厳密な同一Originを要求。
- `fafnir --json assets status`、`fafnir --json assets search --request request.json`

完全なドロップイン互換ではありません。既定検索は所有素材＋CC0に限定し、ダウンロード先はキャッシュ、実行・エンジン投入は別工程です。Unity／Unreal／Blender／Web向けの構成表は、利用条件・エンジン制限・ファイル欠落を確認してから `ready: true` になります。

## 検証

`tests/test_asset_hub.py` と既存のモデル・OAuth・MCP・拡張機能テストで、権利区分、所有素材優先、ソース断時のローカル検索、繰り返し同期、注文URL拒否、パス範囲、エンジン制限、容量・不完全取得、ZIP検査、glTF依存ファイル、生成の一度だけの送信、認証再読込、HTTP Origin、実MCP通信を検証します。

ブラウザーでは、隔離したテストデータで検索、接続設定への移動、課金前の生成確認、実行ガード、キャンセル復帰を操作しました。画面の記録は `docs/images/asset-hub-qa.png`（テスト素材）です。

公開検索APIの実通信は確認済み。各購入サイトの実アカウント全件同期、実OAuth承認、実APIキーによる有料生成は未検証です。この拡張作業では課金ジョブ、Unityビルド、大容量素材取得を実行していません。

## 全件同期の検証と参照

- `tests/test_asset_hub_sync.py`：SQLiteの原子的なページ受領、同一ページ再送、プロセス再生成、順序不正、空ライブラリ、終端証跡、権利・ファイル保持、プライベート情報の拒否。
- `node --test tests/test_owned_library_sync.cjs`：3サイトの継続走査、MV3再生成後の再開、ネイティブ応答喪失、取得中の一時停止、ページループ、ログイン切れ、429、バンドル分割、許可前のアクセス禁止。
- `tests/serve_owned_sync_qa.py`：実UI・実コーディネーター・実Nativeハンドラー・一時DBを接続するGUI検証。ブラウザーAPIとサイト応答は検証データ。BOOTHの実DOMParserによる8ケースも表示。5分で終了し、アカウント・素材ファイル・本番DBは利用しない。
- 実アカウントでの接続、実サイトの全ページ走査、実Chromeの権限ダイアログ、ブラウザー本体の終了／再起動は未検証。Chrome APIの動作はスタブを使った回帰検証と区別する。

参照した一次実装・公式資料（2026-10-17確認）：[BOOTHライブラリ解析の実装元](https://github.com/ribeKim/booth-library-manager/blob/main/utils/parser.ts)、[Fab取得ライブラリの走査実装](https://greasyfork.org/en/scripts/590178-fab-export-entire-owned-asset-library-as-csv/code)、[LutrisのHumble連携](https://github.com/lutris/lutris/blob/master/lutris/services/humblebundle.py)、[Humble注文の構造を扱う実装](https://github.com/xtream1101/humblebundle-downloader/blob/main/humblebundle_downloader/download_library.py)、[Fab公式の購入・取得説明](https://dev.epicgames.com/documentation/fab/purchasing-and-downloading-assets-in-fab)。これらの外部コードはインストール・実行していません。内部APIは将来変更される可能性があります。
