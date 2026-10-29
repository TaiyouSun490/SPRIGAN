# Fafnir 構成生成・検索仕様

> 対象: 検索経路、構成生成、CLIの詳細を確認する上級利用者・開発者。
> 初めて使う場合は先に[利用ガイド](USER_GUIDE.md)を参照してください。

Fafnir Asset Stack Managerは、作りたいゲームの説明とローカルのUnityプロジェクトから、必要機能と実装候補を整理する独立ツールです。カタログはローカルのSQLiteデータベースへ保存します。

## 起動

```powershell
cd C:\path\to\SPRIGAN
.\.venv\Scripts\Activate.ps1
fafnir ui
```

ブラウザで `http://127.0.0.1:8770/` が開きます。ブラウザを自動で開かない場合は `fafnir ui --no-browser` を使います。

GUIでは次の順に使います。

1. 作りたいゲームを文章で入力する。
2. ターゲットと優先方針を選ぶ。
3. 必要ならUnityプロジェクトのルート（`Packages` と `ProjectSettings` があるフォルダー）を入力して「診断」を押す。
4. 「構成を生成」を押す。
5. 要件、3種類の選定候補、候補ごとの根拠と注意点を比較する。

## データソース

- Local: `Packages/manifest.json`、`packages-lock.json`、Unityのプロジェクト設定から導入済み構成を読み取ります。
- GitHub: 公式REST APIで公開リポジトリを検索します。環境変数 `GITHUB_TOKEN` がある場合は認証付きで利用します。
- OpenUPM: 公式パッケージレジストリの検索APIを利用します。
- Unity Asset Store（所有・購入未確認候補）: 所有確認はUnity Editorブリッジ、商品説明・版・互換性表・依存・価格・集計評価は公式公開商品ページから取得します。検索結果一覧は巡回せず、ローカルカタログにある商品IDだけを低速・差分更新します。
- Unity My Assets（所有一覧）: 付属のUnity Editorブリッジが、ログイン中のPackage Managerサービスから所有商品をページング取得します。

### 所有アセットの自動同期

Unity Package Managerの「Add package from disk」で
`unity_package/com.taiyousun.stackforge/package.json` を選びます。Unity Hub / Editorへ
ログインした状態で `Tools > Fafnir > My Assets Sync` を開きます。既定ではUnity起動中に
6時間ごとに同期し、画面で1〜168時間へ変更できます。購入直後は「My Assetsを同期」で即時更新
できます。FafnirのUIまたはMCPを起動しておけば、変更を検出して全件を
ローカルSQLiteとRAGへ差分登録します。非表示にした商品も別ページとして取得し、商品IDで
重複を除きます。画面の「今すぐ同期を確認」は修復用で、通常の二度目の操作ではありません。

自動同期はUnityのMy Assets応答に含まれる商品名とタグをまず登録し、その後、公式公開
商品ページの説明・主要機能・互換性情報を検索文書へ差分反映します。商品ID、購入日時、
非表示状態、同期時刻は所有状態の管理用メタデータとして保持します。Unity認証トークン、
Cookie、画像、レビュー本文、ダウンロードしたアセットの実データは出力・登録しません。

同期ファイルは既定で次へ書き出されます。

```text
%LOCALAPPDATA%\game-stack-planner\unity-my-assets.json
```

### 手動の購入済みRAG登録

Assetsの「商品候補を登録」で状態を「購入済み」にすると、購入済みRAGへ登録できます。AIは自分で入力した次の情報に加え、公式の公開商品説明・主要機能を検索に使います。

- AI用の別名
- メモ
- 選択した機能カテゴリ

価格・集計評価・互換性表は構造化された選別根拠として保持しますが、画像、レビュー本文、
ダウンロードしたアセットの中身はRAGへ渡しません。公開情報を取得できない廃止・非公開商品は、
ユーザー入力とUnity Editor同期由来の商品名・タグだけで検索します。

手動登録時には「私はこの商品を購入済みです（自己申告）」の確認が必須です。これはUnityによる購入確認ではありません。Unity Editor同期の証拠種別は `unity_editor_my_assets`、手動登録は `user_asserted` として区別します。また、ローカルキャッシュに `.unitypackage` が存在するだけでは所有済みRAGへ登録されません。

構成結果の「外部補完」から「候補を追加」を選ぶと、その検索の
機能カテゴリ（ネットワーク、セーブ、XRなど）を引き継いで保存します。保存した商品は
次回以降の構成案で、所有済み・検討中の候補として再利用されます。同一商品は
Asset Storeの商品IDで識別し、レビュー画面や追跡パラメータ付きURLも正規の商品URLへ
まとめるため、重複登録されません。

検索結果や手動保存商品は、既定では次へ蓄積されます。

```text
%LOCALAPPDATA%\game-stack-planner\catalog.sqlite3
```

`--db` を使うと別のデータベースを指定できます。

## CLI

```powershell
# Unityプロジェクトを診断
fafnir scan C:\Projects\MyGame

# オンライン検索を含む構成案
fafnir recommend --prompt "4人協力型ローグライト" --project C:\Projects\MyGame --platform pc

# 保存済みデータだけで構成案
fafnir recommend --prompt "Quest向けVRパズル" --platform quest --offline

# カタログ検索
fafnir catalog --source openupm --query networking

# 購入済みRAGを検索
fafnir --json rag-search --query "装備 重量" --limit 10

# UI/MCP起動時にモデル取得と差分索引を自動実行。進捗確認と明示修復
fafnir --json rag-status
fafnir --json rag-index

# Unity Editorが作成した標準同期ファイルを取り込む
fafnir sync-my-assets

# Asset Store商品を手動保存
fafnir pin "https://assetstore.unity.com/packages/..." --title "商品名" --ownership owned

# 公式公開メタデータの進捗確認・差分更新
fafnir --json asset-details-status
fafnir --json asset-details-sync --limit 20

# ローカルunitypackageを展開せず検査
fafnir --json scan-cache --inspect

# 所有アセットをUnityプロジェクトへ照合（コンパイルは明示指定時だけ）
fafnir --json validate-asset asset_store:12345 --project C:\Projects\MyGame
fafnir --json validate-asset asset_store:12345 --project C:\Projects\MyGame --compile
```

各コマンドに `--json` を付けると機械可読JSONで出力します。

通常はUIまたはMCP起動時に、商品名・タグ・別名・メモ・カテゴリと公式公開商品説明を
多言語Embeddingへ自動変換し、正規化済みベクトルをローカルSQLiteへ保存します。
検索時は質問も同じ固定世代でEmbedding化してコサイン類似度順に返します。
現行世代が全件揃うまでは所有確認済み文書だけを`lexical_fallback`として返し、
Dense類似度とは明確に区別します。部分ベクトルは検索へ混ぜません。`rag-index`は明示的な修復用です。
ベクトル、認証情報、ローカルパス、画像、レビュー本文、アセット実データはMCP応答へ含めません。

## UIとMCPの使い分け

通常のCodex利用ではローカルUIを開く必要はありません。MCPから状態確認、所有アセット検索、
構成案の根拠取得、商品詳細更新、プロジェクト互換性検査、承認付き導入まで実行できます。
UIは大量の候補を目視比較したい場合、同期を手動修復したい場合、導入差分を画面で確認したい
場合の補助作業台です。Asset Storeサイトは購入、ライセンス原文・レビュー本文の確認、Unity公式
導入フローに必要です。

## 推奨結果の見方

### 要件・候補の根拠グラフ

構成生成時に、要件、候補、対象プラットフォーム、商品情報に明記された依存商品をノードと関係としてまとめます。要件と候補の `may_support` はカテゴリ・文字列・RAGから得た**候補仮説**であり、実際に機能するという保証ではありません。互換性が未確認の候補や、所有・導入状態が未確認の依存もそのまま区別します。グラフは推薦結果の `planning_graph` に入り、推薦履歴とともにローカルDBへ保存されます。UIには選定根拠と依存の未確認項目を表示し、MCPの `retrieve_game_stack_evidence` にも同じ関係が含まれます。

候補検索は一度読み込んだカタログにカテゴリ・単語の逆引き索引を作り、要件に関連する候補とRAGの結果を合流します。意味検索だけで選ばず、既存の互換性・所有状態の根拠も適用します。構成の組み立ては上位候補を限定してビーム探索し、各要件の適合度と追加導入のコストを比較します。一つのアセットが複数要件を担当するのは、それぞれに候補根拠がある場合だけです。関連性が低い候補で不足を埋めるより、未充足として残します。

「森」「都市」などの舞台は少数の定義済み語句から手がかりとして推定し、環境アセットの候補検索と採点に反映します。語句による推定であり、舞台の意味や複雑な条件を完全に理解するものではありません。「オンライン不要」「セーブなし」など明示された除外は `excluded_requirements` として返します。不足した要件に限ってAsset Storeの検索リンクを提示します。オンライン検索を有効にした場合のGitHub・OpenUPMの検索は、引き続き抽出された要件を対象に行います。

依存関係はAsset Storeの公開商品情報、検査済みアーカイブ内の `package.json`、選択プロジェクトの `packages-lock.json` を根拠別にたどります。間接依存、循環、共通依存、プロジェクトの解決済み版、明示的な最低版との比較を表示します。比較できるのは安定版の数字形式のみで、範囲指定・プレビュー版・Git参照は未判定です。Asset Store依存の対象環境について商品情報がある場合はその判定も表示します。UnityスキャンのRender Pipelineは導入パッケージによる推定なので、有効なパイプラインが確定したとは扱いません。共通依存を持つことは相互動作の証明ではありません。探索には上限があり、到達時は結果に警告が出ます。

ローカル検査結果は対象プラットフォームとUnity・パッケージ設定の指紋が一致する場合に参照します。過去の形式や構成変更後の結果は再検査が必要です。指紋はプロジェクトの全アセットやソースファイルの変更を網羅しないため、検査時点以降の動作保証ではありません。

アセット一覧の「画風タグを編集」で複数のタグを保存できます。手動タグと、商品名・説明などからの画風推定は別々に表示します。リアル、ローポリ、アニメ調などの既知の表現は同義語を構成生成に反映し、それ以外の自由タグも一覧検索できます。タグを保存した後は構成を再生成してください。自動推定は画像を解析した結果ではありません。

このグラフは既存メタデータから生成するもので、未記載の依存、競合、実装可能性やアセット同士の連携を自動検証するものではありません。データがない場合は不明のままであり、依存がないことを証明しません。

開発時には `PlanningPolicy` の `beam_width`、`options_per_requirement`、`minimum_relevance`、導入コスト、`scene_match_bonus`、`scene_contrast_penalty` を `GameStackPlanner(..., planning_policy=...)` で変更できます。既定値と採点した候補数は `planning_diagnostics` に含まれます。ビーム探索は近似解で、最適解の保証はありません。実装可能性の判定は実プロジェクトで別途行ってください。

知識グラフ上で、依存先の対象環境に明示的な非対応がある場合と、Unityの解決済み版が要求された最低版を下回る場合は、構成比較にコストを加えます。コストは `dependency_incompatible_cost` と `dependency_version_cost` で設定でき、同じ依存問題は構成内で一度だけ数えます。版不足は更新で解消する場合もあるため、候補を強制的に除外しません。情報が未確認というだけでは減点しません。

代替候補には、[MMR](https://www.cs.cmu.edu/afs/cs/Web/People/jgc/publication/MMR_DiversityBased_Reranking_SIGIR_1998.pdf)を参考に、同じ検索区分内でタイトルの重なりが非常に大きい候補を軽く後ろへ動かす処理を加えています。依存の問題を考慮した後の先頭候補は維持し、適合度そのものを書き換えません。タイトルの類似は重複の証明ではありません。`alternative_diversity_penalty` で調整または無効化できます。

研究との対応、評価データの形式、ローカル評価の実行方法は [検索と構成生成の研究・評価](SEARCH_RESEARCH.md) を参照してください。

- 手持ち・導入済み優先: 既存プロジェクトをなるべく崩さない構成。
- OSS・無料優先: OpenUPMとライセンスを確認できるGitHub候補を重視した構成。
- 保守性・低リスク: 更新時期、ライセンス、互換性警告の少なさを重視した構成。

スコアは採用判断そのものではありません。Unityバージョン、Render Pipeline、対象プラットフォーム、ライセンス、最終更新を確認するための比較材料です。
