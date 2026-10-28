# NVIDIA SimReadyの検索と閲覧

「3D MODELS」で検索元に **NVIDIA SimReady** を選ぶと、NVIDIAの公開OpenUSDデータセットの素材を名前で検索できます。初回検索時に公開APIからフォルダー名だけを索引し、以降はSQLiteの索引を使います。「NVIDIA索引を更新」で最新のリビジョンに更新します。更新が途中で失敗しても、前回の索引を残します。

検索結果は候補であり、所有・商用利用許可・UnityやQuestでの動作を意味しません。検索結果の詳細を取得したとき、各パッケージのメタデータにCC BY 4.0と明記されていれば `cc-by` と表示し、帰属表示が必要なことを示します。その他のライセンスは未確認として扱い、必ず配布元で確認します。画像は実在する公開サムネイルを使い、見つからない場合は配布元へのリンクを表示します。

本体と依存するテクスチャ等のダウンロード・Unityへのインポートは、この機能では行いません。配布元の個別ページで全依存ファイルと条件を確認してください。OpenUSD/MDLがUnityやQuestですぐ使えるという保証はありません。メタデータの索引とサムネイルの取得のみで、データセット全体を複製しません。

MCPでは `refresh_nvidia_simready_index`、`search_3d_models`（`source: nvidia_simready`）、`get_3d_model_details` を利用できます。CLIでは `fafnir models refresh_nvidia_index` で更新できます。FAFNIRが起動中の場合、更新したコードを読み込むにはFAFNIRを再起動し、MCPクライアントも再接続してください。

公開元：[NVIDIA SimReady Assets](https://huggingface.co/datasets/nvidia/simready-assets) / [SimReady Central](https://docs.omniverse.nvidia.com/simready/latest/simready-central.html)。
