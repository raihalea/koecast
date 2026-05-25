# 段階6 実装計画 — 段階6-2 以降の小段階分割

`docs/stage6-readiness.md` §3 の進路（**動く**、すなわち Riva ASR NIM を採用する
本筋）で段階6 を進めるにあたり、サーバ・クライアント実装本体をどういう順序の
小段階に分割するかを定める。段階6-1（protocol クレート分離）は完了済み。

実装中に小段階の境界・順序が動いたら本ドキュメントを改訂しながら進める
（骨組みフェーズの `stage6-readiness.md` と同じ運用）。

関連ドキュメント:
- `docs/stage6-readiness.md` — 段階6 着手前のチェックリストと縮退設計、
  Riva 実機検証の結果、§3 で確定した「動く」進路の責務分割
- `docs/dictation-gateway-protocol-v1.md` — WebSocket プロトコル仕様 v1
- `docs/monorepo-design.md` — リポジトリ全体設計（§4 server、§5 client）

---

## 1. 大方針

### 1.1 順序: サーバ → クライアントの直列

**サーバを先に実装する**。理由:

1. **単体で結合確認できる手段が揃う**。wscat とテスト用 Python ストリーミング
   クライアント（検証フェーズで作った `verify_streaming.py` の構造を流用）で、
   WebSocket / Riva / LLM 経路を end-to-end でテストできる。
2. **クライアントは「サーバが返してくる partial/final/formatted」を前提に書く
   必要がある**。サーバが先に動いていればクライアント側 (ws.rs / overlay.rs /
   inject.rs) を実装したそばから挙動を観測できる。
3. **first partial レイテンシ再測定**（検証フェーズからの繰り越し）はクライアントの
   `audio.rs` + `hotkey.rs` がないと厳密にはできないが、それまではサーバ単体で
   ファイル投入による計測（検証フェーズと同条件）が可能。サーバ側に隠れた性能
   問題があれば早期に気付ける。

### 1.2 6-2 と 6-3 は直列、並行禁止

段階6-3 着手の前に、**段階6-2 を完全完了**させる。完全完了の判定基準は次のとおり:

- `server/deploy/` の compose 一式で **Riva NIM + ゲートウェイ** が立ち上がる
- host から `wscat` で WebSocket 接続でき、`start` → 音声フレーム送信 → `stop`
  に対して `partial` / `final` / `formatted` / `session_end` が想定通り返る
  end-to-end 経路が確認できる
- `just test-shared`（server pytest + client `cargo test -p koecast-protocol`）が
  緑のまま維持

この状態に到達するまで段階6-3 には入らない。

### 1.3 小段階の粒度

骨組みフェーズと同程度の粒度（1日〜数日）。各小段階で「動く形」になることを
保証し、終了時に止まって確認を待つ。

### 1.4 契約テストの維持

全小段階で `just test-shared`（server pytest + client `cargo test -p koecast-protocol`）を
**緑のまま維持**。実装で型を増やすなら `protocol/schema/` / `protocol/examples/` /
Pydantic / serde を**同一コミット**で更新する（`monorepo-design.md` §9 の方針）。

段階6-3 以降の Tauri アプリ本体 (`client/src-tauri/`) のビルド・実行は
Mac/Windows の実機で行うため、`devenv shell` (DGX 上の Linux) のテストには
含めない。詳細は §1.5。

### 1.5 段階6-3 以降の開発の場: Mac/Windows 実機への移行

koecast クライアントの最終ターゲットは Mac/Windows であり、Linux ではない。
GUI / マイク / global-hotkey / 別アプリへの inject は本質的に GUI セッションが
あるホスト OS でしか確認できないため、段階6-3 以降は開発の場を以下のように切り分ける:

- **DGX Spark の `devenv shell`**: server (Python) と protocol (Rust serde) の
  ビルド・テスト・契約テストまで担当。`just test-shared` が指す範囲。
- **Raiha の手元の Mac か Windows**: Tauri クライアント本体 (`client/src-tauri/`)
  のビルド・実行・GUI 動作確認 (空ウィンドウ / `ws.rs` / `audio.rs` / `hotkey.rs` /
  `inject.rs` / `overlay.rs` / フロントエンド)。

この切り分けに伴って:

- `devenv.nix` に Linux 用 Tauri OS 依存 (WebKitGTK / libsoup / dbus 等) は
  **追記しない**。`monorepo-design.md` §10 の「Tauri OS 依存は実機ビルド検証時に
  追記」の「実機」は **Mac/Windows** と確定。
- Mac/Windows それぞれの Tauri prerequisites と手元での作業手順は段階6-3-a で
  新規作成する `docs/client-setup.md` に書く。
- 動作確認結果は **テキストコピペを基本**として Claude (DGX 側) に運ぶ。
  ログ・エラーメッセージはテキスト、視覚確認が必要なときのみスクショを追加する。
- どちらの OS から先に着手するかは Raiha が決定して指示する。
- Claude Code を引き続き DGX 上で動かすか、手元 Mac/Windows に移すかも Raiha が
  決める (DGX 側で実装 → git pull で手元実機に持っていく運用が標準)。

**Windows 着手時の留意点 (段階6-3-e の Mac 検証で判明したもの)**:

- 段階6-3-e で **Mac は `enigo` 経由の `Cmd+V` 送信中にアプリが silent abort**
  したため、macOS だけ `osascript` 経由 (System Events への AppleScript
  keystroke) に切り替えてある (`client/src-tauri/src/inject.rs` の
  `#[cfg(target_os = "macos")]` 分岐)。
- **Windows 版は `enigo` のまま** (`#[cfg(not(target_os = "macos"))]`)。Windows
  実機検証時は **enigo の `Ctrl+V` 送信が同じ問題 (silent abort) を起こさないか
  必ず確認**すること。再現するなら Windows 用の代替経路 (例: WinAPI 直接で
  `SendInput`, or `nircmd` 等の外部ツール経由) を用意する。
- 上記が確定したら、本ドキュメントと `docs/client-setup.md` (Windows 章) を
  更新する。

---

## 2. 小段階の分割

### 2.1 段階6-2: サーバ実装本体 (`server/`)

#### 段階6-2-a: 設定ロード + WebSocket スケルトン + 接続状態機械

- **実装**:
  - `config.py`: env / 設定ファイル読み（Riva アドレス / LLM エンドポイント /
    bind addr / 辞書 / システムプロンプト）
  - `__main__.py`: エントリポイント
  - `server.py`: WebSocket サーバ + 接続状態機械
    （CONNECTING → READY → LISTENING → READY → CLOSED）
    - `ready` 送信、`start` 受信、`stop` 受信、`session_end` 送信、`ping`/pong
    - PCM Binary フレームは受信して捨てる（まだ Riva なし）
    - エラー時の `error` メッセージ送信（`BAD_MESSAGE` / `UNSUPPORTED_VERSION`）
  - 既存の `protocol.py` (Pydantic) を実装本体で初使用
- **動作確認**:
  - `uv run python -m dictation_gateway` で起動
  - `wscat -c ws://localhost:8000/v1/dictation` で接続
  - `ready` 受信、`{"type":"start","protocol_version":1}` → `{"type":"stop"}` →
    `session_end` の往復
  - 未知 `type` → `BAD_MESSAGE`、`protocol_version=2` → `UNSUPPORTED_VERSION`
- **手元作業**: なし（Riva も LLM も不要）

#### 段階6-2-b: `riva_bridge.py` + Riva NIM 結合 + Riva NIM compose

検証フェーズで判明した責務（CJK 空白除去 / segment_id 採番 / Word Boosting 不使用）
を**全部織り込む**。

- **実装**:
  - `riva_bridge.py`:
    - `nvidia-riva-client` で gRPC 50051 に接続、ストリーミング送受信
    - `language_code="ja-JP"` / `enable_automatic_punctuation=True` /
      `interim_results=True`
    - **CJK 文字間空白除去**（regex で除去、partial/final 両方に適用）
    - **`segment_id` 採番**: 接続開始時 0、`is_final=True` ごとにインクリメント、
      再接続でリセット
    - **Word Boosting (`speech_contexts`) は使わない**（検証で ja に効かないと確定）
  - `server.py` から PCM Binary フレームを `riva_bridge.py` に流す結線
  - partial → `partial` メッセージ、`is_final=True` → `final` メッセージで
    WebSocket 経由送信（`segment_id` 付き）
  - エラー処理: `RIVA_UNAVAILABLE` / `RIVA_STREAM_ERROR`
  - **`server/deploy/riva-nim.compose.yml`**: `stage6-readiness.md` §2.0 の
    ひな形を起点に、Riva NIM 起動の compose を作成
    - イメージ `:1.4.0` 固定、`NIM_TAGS_SELECTOR="mode=str"`、
      bind mount は host で事前 mkdir、ports 50051 + 9000、shm 8gb、
      nvidia runtime + deploy.resources
- **動作確認**:
  - Riva NIM コンテナ起動: `docker compose -f server/deploy/riva-nim.compose.yml up -d`
  - サーバ起動 → テスト用 Python ストリーミングクライアント（`verify_streaming.py`
    の構造を再利用、ただし Riva 直接ではなくゲートウェイ経由 WebSocket）で
    16kHz mono PCM の WAV を流す
  - partial が逐次返り、`final` で空白除去済み・`segment_id` 単調増加のテキスト
    が返ることを確認
- **手元作業**:
  - host で `mkdir nim-cache`（UID 1000）
  - `export NGC_API_KEY=<key>`
  - 必要なら `docker login nvcr.io`（`~/.docker/config.json` の資格情報が残って
    いれば不要）

#### 段階6-2-c: `llm.py` + formatted 整形 + fallback

- **実装**:
  - `llm.py`: LLM 整形クライアント
    - 想定エンドポイント: ローカル `qwen36-mtp` (llama-server) の
      OpenAI 互換 API（`/v1/chat/completions`）
    - 失敗・タイムアウト時は `formatted` を `fallback=true` で `final` 原文を返す
  - `server.py` から `final` を `llm.py` に渡し、整形結果を `formatted` メッセージ
    として WebSocket に送信（`segment_id` を `final` と揃える）
  - `start.context` の用語辞書を整形プロンプトに織り込む
    （検証フェーズで確定した「Word Boosting の代替先」、
    `dictation-gateway-protocol-v1.md` §4.1）
  - `enable_formatting=false` 指定時は `formatted` をスキップ
  - エラー処理: `LLM_UNAVAILABLE`
- **動作確認**:
  - wscat → `start` → 音声 → `stop` で final → formatted の順で返る
  - LLM を意図的に止めて `fallback=true` 動作を確認
- **手元作業**: **既存の `qwen36-mtp` の docker compose で起動する**
  （検証フェーズで停止状態のままなら起動）

#### 段階6-2-d: `server/deploy/docker-compose.yml`（ゲートウェイ本体の compose）

Riva NIM 用 compose は 6-2-b で作成済み。本小段階の責務はゲートウェイ本体に限定。

- **実装**:
  - `server/deploy/docker-compose.yml`: **koecast ゲートウェイ本体**の compose
  - `server/Dockerfile`: uv + Python 3.14 + `dictation_gateway` のイメージ
  - **起動順序制御**: ゲートウェイから Riva NIM への接続は
    `riva-nim.compose.yml` 側のサービスが先に ready になっている必要がある。
    両 compose を 1 つの `docker compose -f gateway.compose.yml -f
    riva-nim.compose.yml up` で起動した際の `depends_on` + healthcheck
    （Riva の `/v1/health/ready`）を設計する
- **動作確認**:
  - 両 compose 同時起動でゲートウェイと Riva NIM が共に立ち上がる
  - 外部（host）から `wscat -c ws://localhost:<gateway-port>/v1/dictation`
    で接続できる
  - 6-2-b 〜 6-2-c で確認した end-to-end 動作が compose 経由でも再現できる
- **手元作業**: なし

#### 段階6-2 完全完了の判定（段階6-3 着手の条件）

§1.2 の通り、本小段階終了時点で:
- compose 一式で Riva NIM + ゲートウェイ + LLM が動く
- wscat で `start` → 音声 → `stop` → `partial` / `final` / `formatted` /
  `session_end` の end-to-end 経路が通る
- `just test-shared` 緑

を満たしたら段階6-3 へ進む。満たさなければ満たすまで段階6-2 を継続する。

#### 段階6-2 完了時点の運用上の注意・申し送り

段階6-4-b の運用ドキュメント整備を待たずに、段階6-2 実装で生じた前提依存と既知事項を
ここに残しておく。**段階6-3 でクライアント結合確認をするときに参照すること。**

- **gateway compose は qwen36-mtp のネットワーク (`hermes-stack_default`) に
  external attach している** (判断保留 #5)。
  - `server/deploy/docker-compose.yml` の `networks.llm_backend.name =
    hermes-stack_default` (`external: true`) を経由して
    `qwen36-mtp:8080` を DNS 解決している。
  - **qwen36-mtp 側の compose を再構築してネットワーク名が変わると、gateway が
    LLM に到達できなくなる**。その場合は formatted が常に `fallback=true` に
    なって全 final が原文のままになる (協調的縮退するので接続は閉じない)。
  - 復旧手順:
    1. `docker network ls` で qwen36-mtp の所属ネットワーク名を確認
    2. `server/deploy/docker-compose.yml` の `networks.llm_backend.name` を
       新ネットワーク名に書き換え
    3. `docker compose -f docker-compose.yml -f riva-nim.compose.yml up -d` で
       gateway だけ recreate される
  - 中長期では qwen36-mtp 側 compose の所有方針 (本リポジトリに取り込むか、
    別管理を続けるか) を決めて、後者なら本依存をドキュメント (6-4-b の
    `docs/operation.md`) に格上げする。

- **qwen36-mtp の `Up (unhealthy)` 表示は調査済み、実害なし** (2026-05-25 確認)。
  - 経緯: 段階6-2 自走実行中、`docker compose ps` で qwen36-mtp に `(unhealthy)`
    ラベルが付いていた。一方、LLM 整形 (`scenario_streaming_ja_with_wav_and_llm`) は
    成功しており、`/v1/chat/completions` の疎通自体は問題なかった。
  - 人間側で `docker compose logs qwen36-mtp` を確認した結果、LLM 本体は正常:
    `/v1/chat/completions` は 200 応答、モデルロード成功、MTP speculative decoding も
    期待通り動作している。
  - `(unhealthy)` 表示は qwen36-mtp 側 compose の **healthcheck 定義が llama.cpp の
    実態と噛み合っていない**ことに由来するもので、LLM 機能には実害なし。
  - したがって段階6-3 で LLM 起因の不具合 (formatted が空 / fallback 頻発 /
    timeout 等) が出たとしても本件は原因ではない。別途調査すること。

---

### 2.2 段階6-3: クライアント実装本体 (`client/src-tauri/`)

#### 段階6-3-a: tauri 復活 + `docs/client-setup.md` 新規 + Mac/Windows で空ウィンドウ起動

§1.5 のとおり Tauri ビルド・実行は Mac/Windows 実機で行うため、`devenv.nix` への
Linux 用 OS 依存追記は**やらない**。

- **実装 (DGX 上で実施)**:
  - `client/src-tauri/Cargo.toml`: `tauri` / `tauri-build` を復活
    （Cargo.toml 内のコメントに従って `[build-dependencies]` / `[dependencies]`）
  - `client/src-tauri/build.rs`: 標準の `tauri_build::build()` 1 行
  - `client/src-tauri/src/main.rs`: 最小の Tauri 起動（空ウィンドウ）
  - `client/tauri.conf.json` の基本確認
  - **新規 `docs/client-setup.md`**: Mac/Windows それぞれの Tauri prerequisites
    (Mac: Xcode CLT、Windows: Visual Studio Build Tools (C++) + WebView2 Runtime)、
    リポジトリ clone / pull の運用、必要な OS 権限 (アクセシビリティ / マイク /
    入力監視 等)、`cd client && npm install && npm run tauri dev` の手順、
    動作報告の運搬方法 (テキストコピペ基本、視覚確認のみスクショ)
- **DGX 上の確認**:
  - `cd client && cargo check -p koecast-protocol` (protocol だけビルド可能か)
  - `just test-shared` 緑維持 (`src-tauri` クレートは Linux でビルドできないため
    `--workspace` ではなく `-p koecast-protocol` でテスト)
- **Mac/Windows 実機での動作確認 (Raiha 手元)**:
  - `docs/client-setup.md` の手順に従って prerequisites を整備
  - `cd client && npm install`
  - `cd client && npm run tauri dev` で空ウィンドウが立つこと
  - 結果報告: 起動ログのテキストコピペ + 空ウィンドウのスクショ 1 枚
- **手元作業**: Mac/Windows 実機での実行と報告

#### 段階6-3-b: `ws.rs` + 接続状態機械 + `config.rs` + 最低限の Svelte 表示

- **実装 (DGX 上で実施)**:
  - `client/src-tauri/src/ws.rs`: `tokio-tungstenite` で WebSocket 接続、
    指数バックオフ再接続、`ready`/`partial`/`final`/`formatted`/`session_end`/
    `error` 受信
  - `client/src-tauri/src/config.rs`: 設定ファイル読み
    （サーバ URL / ホットキー / 辞書）
  - `client/src/App.svelte`: サーバ URL 表示 + 接続状態表示の最小 UI
  - Rust ↔ Svelte は Tauri command で
- **DGX 上の確認**: `just test-shared` 緑維持。
- **Mac/Windows 実機での動作確認 (Raiha 手元)**:
  - DGX 上のサーバ compose が稼働している状態で Tauri アプリ起動 → "READY" 表示
  - サーバを止めて再起動 → 指数バックオフで自動再接続
  - 結果報告: 接続状態表示 + アプリログをテキストコピペ
- **手元作業**: Mac/Windows 実機での実行と報告 (DGX 側 server compose 稼働前提)

#### 段階6-3-c: `audio.rs` + `hotkey.rs` ★first partial レイテンシ再測定

- **実装 (DGX 上で実施)**:
  - `client/src-tauri/src/audio.rs`: `cpal` でマイク取得、16kHz mono PCM16 に変換、
    100 ms チャンクで `ws.rs` に渡す
  - `client/src-tauri/src/hotkey.rs`: `global-hotkey` でプッシュトゥトーク
    （押下: `start` + 録音開始、解放: `stop` + 録音停止）
- **Mac/Windows 実機での動作確認 (Raiha 手元)**:
  - DGX 側のサーバ compose + Riva NIM が稼働中の状態でアプリ起動
  - 初回マイク権限 / アクセシビリティ権限のプロンプトに同意
  - ホットキー押下 → 発話 → 解放
  - 結果報告: アプリログ (押下時刻 / first partial 受信時刻 / final 内容) を
    テキストコピペ
- **★first partial レイテンシ再測定**:
  - 計測方法: ホットキー押下時刻 → 最初の `partial` 受信時刻の差分。
    **クライアント側 (Mac/Windows) のログを中心**に判定する。片側ログで成立する。
  - 比較対象: 検証フェーズで観測した **2.84 s（ファイル投入）**
  - **判定は体感ベース**。許容ラインは 6-3-c 到達時に Raiha が実機で判断する。
    数値の厳密な照合は深追いしない。
  - **時計同期について**: クライアント側ログ中心の判定なのでクライアント側の
    タイムスタンプだけで成立する。サーバ側ログと突き合わせる必要が出たときに
    限り、Mac/Windows と DGX の NTP 同期を確認する。
  - 超過時の打ち手（試す順）:
    1. Riva NIM の `endpointing_config` チューニング (DGX 上)
    2. `NIM_TAGS_SELECTOR="mode=str-thr"` で str-thr プロファイル比較 (DGX 上)
    3. クライアント overlay 表示戦略（録音中ステータスで体感を補う）
- **手元作業**: Mac/Windows 実機での実行と報告 (Riva NIM は 6-2-b の compose を
  そのまま使用)

#### 段階6-3-d: `overlay.rs`（partial 表示）

- **実装 (DGX 上で実施)**:
  - `client/src-tauri/src/overlay.rs`: 枠なし最前面の別ウィンドウ
  - Svelte 側に overlay 用ルートを作り、`ws.rs` から受けた partial/final/formatted を
    逐次表示
- **Mac/Windows 実機での動作確認 (Raiha 手元)**:
  - 発話中にオーバーレイに partial がリアルタイム表示
  - segment 境界で `final` / `formatted` に置き換わる
  - 結果報告: オーバーレイ表示のスクショ + アプリログをテキストコピペ
- **手元作業**: Mac/Windows 実機での実行と報告

#### 段階6-3-e: `inject.rs`（クリップボード退避 → ペースト → 復元）

- **実装 (DGX 上で実施)**:
  - `client/src-tauri/src/inject.rs`: `arboard` でクリップボード退避、
    `formatted` テキストを書き込み、`enigo` で Ctrl+V キーストローク送信、
    退避内容を復元
- **Mac/Windows 実機での動作確認 (Raiha 手元)**:
  - メモ帳 / VSCode / Slack 等でカーソルを合わせて発話 → 確定テキストが
    ペーストされる
  - クリップボードに元入っていた内容が復元されている
  - 結果報告: 注入結果と復元の確認をテキストコピペ
- **手元作業**: Mac/Windows 実機での実行と報告 (DGX 側で LLM (qwen36-mtp) 稼働中
  前提、6-2-c で起動済み)

#### 段階6-3-f: Svelte 設定画面の充実

- **実装 (DGX 上で実施)**: サーバアドレス / ホットキー設定 / 用語辞書 /
  システムプロンプト 編集 UI
- **Mac/Windows 実機での動作確認 (Raiha 手元)**:
  - 設定変更 → 再接続 → 反映
  - 結果報告: 変更前後の挙動差をテキストコピペ
- **手元作業**: Mac/Windows 実機での実行と報告

---

### 2.3 段階6-4: Mac で仕上げる (機能改善 + 配布パッケージ化 + ドキュメント)

段階6-3-f で Mac クライアントのコア体験 (ホットキー → 喋る → overlay 表示 →
formatted 注入) が動き、設定画面まで揃った。段階6-4 は **Mac で日常運用に耐える
仕上げ** を行う 4 小段階に分割する。各段階の終わりで止まって承認待ち、承認後に
commit、その後次の小段階へ、という運用は段階6-3 と同じ。

各小段階の入力となる「6-3 までの申し送り」は本ドキュメント末尾の §5 (申し送り
台帳) に集約してある。本セクションの各小段階は「§5 のどの宿題を回収するか」を
明示する。

---

#### 段階6-4-a: 実用テスト期間 (Raiha が使い込む)

`monorepo-design.md` §5 で予告されていた「Tauri 起動 + トレイ常駐」を本小段階
の入口で実装してから、Raiha の使い込みフェーズに入る 2 段構成にする。

##### 6-4-a-i: メニューバー常駐の最小実装 (Claude Code)

**Claude Code が実装する**。`cargo run` のまま常駐できる「ターミナルなしで
1 日使える」最小構成。`.app` バンドル化やログイン時自動起動 (launchd) は 6-4-c
以降に分離する (アクセシビリティ権限再付与問題を巻き込まないため)。

スコープ:

- macOS の **accessory アプリ相当**で起動。Dock にアイコンを出さない
  (`set_activation_policy(ActivationPolicy::Accessory)`)。
- メニューバーにアイコン常駐 (`TrayIconBuilder`)。
- 起動時 main window は **hidden**。アイコン左クリックで設定 window を
  show/hide トグル、右クリックで `設定を開く` / `終了` のメニュー。
- main window を閉じてもアプリは終了しない。トレイメニューの `終了` のみで
  exit する (window close event を hide にすり替え + `RunEvent::ExitRequested`
  対策)。
- overlay window はホットキー押下時に show (従来動作のまま、変更なし)。
- **接続状態のアイコン色/バッジ表現は裁量**。Tauri 2 でアイコン動的変更が
  素直に書けるなら入れる、手間取るなら a-i では省いて 6-4-b か 6-4-c に回す。
  a-i の核心は「ターミナルなしで常駐して 1 日使える」ことなので、最小に徹して
  よい。

完了基準 (Mac 実機で確認):

- メニューバーに常駐する
- 設定 window が開閉できる
- main window を閉じてもアプリが終了しない
- ホットキー押下で overlay が出る (従来どおり)
- トレイメニューから明示的に終了できる
- `just test-shared` 緑

完了後、コミット + push して a-ii に進む。

##### 6-4-a-ii: Raiha が常駐版で使い込み・記録

**Claude Code は実装しない**。Raiha が a-i 完了版で 1 日ベースで使い込み、
問題リストを収集する期間。Claude Code の役割は以下 2 つだけ:

1. **入口**: 「実用テストで見てほしい観点のチェックリスト」(`docs/stage6-4a-
   test-checklist.md`) を提示済み。Raiha はこれを基に記録する。
2. **出口**: Raiha が返してきた問題リストを **6-4-b の入力** として受け取り、
   対策設計に進む。

#### 6-4-a で集める観点 (詳細はチェックリストに切り出す)

- **§5 #1 LLM 整形誤訂正の実例を複数集める**。これは 6-4-b の対策設計の前提
  であり、最重要。**入力音声・final・formatted・期待した結果** を 1 件ごとに
  セットで記録する。サンプルが少ないとプロンプト改善の効き目が判断できない。
  既知の事例: 「ベッドロックでLLMを動かす」→ 「Bedrock EditionでEDを動かす」
  (段階6-3-c)、「テスト注入」→ 「ティエス ト・ジュニア」(段階6-3-e)。
- §5 #2 partial 初出の **キリル/英文字ノイズの体感頻度**。仕様上は final で
  上書きされるが、UI 体感的にどの程度気になるか。suppress するかは体感判定。
- §5 #4 **レイテンシの体感**。検証時計測 ~2.7s から悪化していないか、Riva
  endpointing チューニング / `mode=str-thr` への切替を検討するに値するか。
- **注入の失敗・誤動作**: クリップボード復元漏れ、ペースト先間違い、文字化け、
  キー競合 (Mac ショートカットや IME)。
- **overlay の表示挙動**: フォーカス保持、自動 hide のタイミング、位置・サイズ
  の使い勝手、複数 segment の置換ふるまい。
- **再接続まわり**: gateway 一時停止 → Reconnecting → Ready 復帰、ネットワーク
  切断時の挙動。
- **その他の使用感**: ホットキー押下感覚、設定変更フローの煩雑さ、起動・終了
  の手間 (cargo run なので Dock からの起動はまだできない)。

#### 完了基準
- Raiha がチェックリストに沿って 1 日 (もしくは納得できる期間) 使い込み、
  「6-4-b で潰したい問題リスト」を返してくる。
- DGX 要否: gateway + parakeet + qwen36-mtp 常用。

---

#### 段階6-4-b: 機能改善 (Mac 完結する範囲)

6-4-a で集まった問題リストを入力に、機能修正を実装する。**Mac で完結する範囲**
に限る (Windows は段階6-5)。

- **§5 #1 LLM 整形誤訂正対策** (6-4-a の収集事例を起点):
  - gateway 側 `server/src/dictation_gateway/llm.py` のプロンプト見直し
  - glossary (`start.context`) のプロンプトへの織り込み方の確認・改善
  - 温度・top_p 等の生成パラメータ調整
  - 必要なら few-shot examples の追加
  - protocol は変更しない (server 内クローズの修正)
- **§5 #2 partial suppress** (6-4-a の体感判定で「やる」になったら): 最初の
  数百ms の partial を抑制する or 信頼度フィルタ。client/overlay 側で実装。
- **§5 #3 設定の即時反映**: 現状「保存後に再起動が必要」。
  - `server_url` 変更 → ws タスク再接続
  - `hotkey` 変更 → global-shortcut の unregister/register
  - `glossary` 変更 → managed state を `Mutex<Config>` 化、次の `start` で読む
- **§5 #4 Riva endpointing / `mode=str-thr` の切替テスト** (DGX 側): 計画書
  §4 の打ち手を 6-4-a の体感判定後に試す。

#### 完了基準
- 6-4-a の問題リストの主要項目が解消、もしくは「次フェーズ送り」を明示。
- DGX 要否: 修正検証で全要素必要。

---

#### 段階6-4-c: 配布パッケージ化 (Mac)

`cargo run` の素バイナリ運用から、Application フォルダに入れて常用できる
`.app` 配布形態に移行する。

- **§5 #5 (Mac分) 本物アイコン作成・差し替え**: 32×32 透明 PNG プレースホルダ
  → ICNS + 各解像度 PNG (16/32/64/128/256/512)。
- **§5 #6 `.app` バンドル化**: `tauri.conf.json` の `bundle.active = true`、
  `bundle.macOS` 設定整備、`npm run tauri build` で `.app` 生成、起動確認。
  署名/notarization は当面省略 (初回起動時の Gatekeeper 手動承認で運用)。
- **§5 #7 `.app` 後のアクセシビリティ権限再付与**: TCC エントリはバイナリ
  パス + チェックサム単位なので、`.app` の中身バイナリで再付与が必要。手順を
  `docs/client-setup.md` に追記。
- **§5 #8 `macos-private-api` の扱い決定**: Raiha 個人利用 (`.app` 直接配布)
  なら維持。Mac App Store 提出の予定が出たら除外。本小段階で方針を明文化。
- **§5 #14 `NSMicrophoneUsageDescription` 確認**: `.app` バンドル後もマイク
  権限プロンプトが正しく出ることを実機確認。

#### 完了基準
- `.app` を `~/Applications` に入れて、`cargo run` なしで日常運用できる。
- DGX 要否: 動作確認に gateway + parakeet + qwen36-mtp。

---

#### 段階6-4-d: 運用ドキュメント整備 + 段階6 完了マーク

- **§5 #11 README 整備**: Mac ビルド/インストール/常用手順、サーバ起動手順、
  ホットキー、設定ファイル所在。
- **§5 #12 `docs/operation.md` 新設**: 日常運用 (compose 起動順序、再接続
  フロー、トラブルシューティング集約)。本ドキュメントには **§5 #9
  (`hermes-stack_default` 外部依存の復旧手順)** と **§5 #10
  (qwen36-mtp の `Up (unhealthy)` 表示は実害なし)** を必ず含める。
- **§5 #13 `docs/stage6-readiness.md` §6 着手トリガーを全 ☑** + 本ドキュメント
  の 6-4 セクションを「完了」マーク。

#### 完了基準
- ドキュメントが揃い、Raiha が空いた時間で再セットアップしても README だけで
  立ち上がる。
- ここで **段階6 (Mac 仕上げまで) を閉じる**。

---

### 2.4 段階6-5: Windows 展開 (独立フェーズ)

Windows 展開は段階6-4 とは独立した大きさのフェーズなので段階6-5 として分離する。
段階6-4 を完了して **Mac で日常運用が安定した状態** で着手するのが現実的
(Mac で出る問題を先に潰してから Windows に展開する方が手戻りが少ない)。
着手のタイミングは Raiha が決める。

#### 段階6-5-a: Windows 環境セットアップと最小起動

- **§5 #15 (前半)**: `docs/client-setup.md` §3 Placeholder を埋める。
  Visual Studio Build Tools (C++ workload) + WebView2 Runtime + Rust stable +
  Node 24 LTS + just のインストール手順、`git clone` / `git pull` の運用。
- `cd client && npm install && npm run tauri dev` で空ウィンドウ起動 (Mac の
  6-3-a 相当)。
- **§5 #17**: 設定ファイル path `%APPDATA%\koecast\config.toml` の動作確認、
  UAC / SmartScreen 初回バイパス手順。

#### 段階6-5-b: Windows 実機機能検証

- 段階6-3-b 〜 6-3-f 相当の機能が Windows で動くか順に確認 (gateway 接続 →
  push-to-talk → overlay → 注入 → 設定保存)。
- **§5 #16 最優先**: `enigo` の `Ctrl+V` 送信が **silent abort しないか検証**。
  Mac で同類の問題があったので Windows でも疑う。NG なら代替経路を実装:
  - Rust `windows` crate で `SendInput` 直接呼び
  - もしくは `nircmd.exe` 等の外部ツール経由
- もし問題が出たら `inject.rs` の `#[cfg(not(target_os = "macos"))]` ブロックを
  Windows 専用 cfg に分ける。

#### 段階6-5-c: Windows 配布パッケージ化

- **§5 #5 (Windows分) ICO 作成**: アイコンの Windows 形式。
- **§5 #15 (後半)**: `npm run tauri build` で `.msi` / `.exe` 生成。
- SmartScreen 初回バイパス手順を `docs/client-setup.md` Windows 章に追記。
- 完了基準: `.msi` をインストールして、`cargo run` なしで Windows 常用できる。
- ここで **段階6-5 (Windows 展開) 完了** = クロスプラットフォーム両対応の
  koecast 完成。

---

## 5. 申し送り台帳 (6-3 までの申し送りを 6-4/6-5 で回収する一覧)

6-4 と 6-5 の各小段階が回収すべき宿題を 1 表にまとめる。新しい申し送りが
出たら本表に追記してから次の小段階で扱う。

凡例: 区分 = **機能 (Func)** / **配布 (Dist)** / **ドキュメント (Doc)**。

| # | 宿題 | 出所 | 区分 | 担当 OS | 回収先 |
|---|---|---|---|---|---|
| 1 | LLM 整形の誤訂正改善 (実例: ベッドロック→Bedrock Edition, テスト注入→ティエス ト・ジュニア) | 6-3-c, 6-3-e | Func | Mac+DGX | 6-4-a 収集 → 6-4-b 対策 |
| 2 | partial 初出のキリル/英文字ノイズ suppress 判断 | 6-3-c, 6-3-d | Func | Mac | 6-4-a 体感 → 6-4-b |
| 3 | 設定の即時反映 (現状は再起動必要) | 6-3-f | Func | Mac | 6-4-b |
| 4 | Riva endpointing / `mode=str-thr` 切替 | 計画書§4, 6-3-c | Func | Mac体感+DGX | 6-4-a 体感 → 6-4-b |
| 5 | 本物アイコン (ICNS / ICO / 各解像度 PNG) | 6-3-a, 6-4-b旧 | Dist | Mac+Win | 6-4-c (Mac), 6-5-c (Win) |
| 6 | `.app` バンドル化 | 6-3-e | Dist | Mac | 6-4-c |
| 7 | `.app` 後のアクセシビリティ権限再付与手順 | 6-3-e | Dist+Doc | Mac | 6-4-c |
| 8 | `macos-private-api` の配布時扱い決定 | 6-3-d, 6-4-b旧 | Dist | Mac | 6-4-c |
| 9 | gateway compose の `hermes-stack_default` 外部依存運用 | 6-2 | Dist+Doc | DGX | 6-4-d (operation.md) |
| 10 | qwen36-mtp の `Up (unhealthy)` 表示 (実害なし、healthcheck 修正は中長期) | 6-2 | Doc | DGX | 6-4-d (operation.md) |
| 11 | README 整備 | 6-4-b旧 | Doc | All | 6-4-d |
| 12 | `docs/operation.md` 新設 | 6-4-b旧 | Doc | DGX+Mac | 6-4-d |
| 13 | `docs/stage6-readiness.md` §6 着手トリガー ☑ + 段階6 完了マーク | 6-4-b旧 | Doc | — | 6-4-d |
| 14 | `NSMicrophoneUsageDescription` の `.app` 後動作確認 | 6-3-c | Dist | Mac | 6-4-c |
| 15 | Windows 環境セットアップ + `.msi`/`.exe` 生成 | 6-3-a | Dist+Doc | Win | 6-5-a (前半) / 6-5-c (後半) |
| 16 | Windows での enigo `Ctrl+V` silent abort 検証 + 代替経路 | 6-3-e | Func+Dist | Win | 6-5-b |
| 17 | Windows 用設定ファイル path (`%APPDATA%`) と UAC/SmartScreen | 6-3-a | Dist+Doc | Win | 6-5-a |

---

## 3. Riva NIM / LLM コンテナ起動が必要なタイミング

| 段階 | Riva NIM | LLM (qwen36-mtp) |
|---|:---:|:---:|
| 6-2-a | — | — |
| **6-2-b** | **★初回起動**（docker login + mkdir nim-cache + NGC_API_KEY） | — |
| **6-2-c** | 継続 | **★起動**（既存 compose 経由） |
| 6-2-d | 継続 | 継続 |
| 6-3-a | — | — |
| 6-3-b | **必須** (gateway compose が `depends_on: parakeet (service_healthy)` で Riva ready を待つため、gateway 単体起動はできない) | — |
| **6-3-c** | **★起動**（リアルタイム ASR 観測） | — |
| 6-3-d | 継続 | — |
| **6-3-e** | 継続 | **★起動**（`formatted` 経路通すため） |
| 6-3-f | 任意 | 任意 |
| **6-4-a** | 継続（常用） | 継続（常用） |
| 6-4-b | 継続 | 継続 |
| 6-4-c | 継続 (.app 動作確認) | 継続 |
| 6-4-d | — (文書整備) | — |
| 6-5-a | — (空ウィンドウ起動) | — |
| 6-5-b | 継続 | 継続 |
| 6-5-c | 継続 (.msi 動作確認) | 継続 |

手元作業の発生点:
- **`docker login nvcr.io`**: 6-2-b 開始時に資格情報がキャッシュされていれば
  不要、消えていれば再ログイン
- **`NGC_API_KEY` の export**: Riva NIM コンテナ起動時（shell rc への永続化を検討）
- **`mkdir nim-cache`**: 検証フェーズで削除したので 6-2-b で再作成
  （host UID 1000）
- **LLM (qwen36-mtp) 起動**: 6-2-c 開始時、既存の docker compose で起動

---

## 4. first partial レイテンシ再測定（検証フェーズからの繰り越し）

- **位置**: 段階6-3-c（`audio.rs` + `hotkey.rs` 実装直後）。Mac/Windows 実機で計測。
- **方法**: ホットキー押下時刻 → 最初の `partial` 受信時刻の差分。
  **クライアント側 (Mac/Windows) のログを中心**に判定し、片側ログで成立させる。
- **比較対象**: 検証フェーズで観測した **2.84 s（ファイル投入）**
- **判定**: 体感ベース。許容ラインは 6-3-c 到達時に Raiha が実機で判断する。
  数値の厳密な照合は深追いしない。
- **時計同期**: クライアント側ログ中心の判定なのでクライアント側タイムスタンプ
  だけで成立する。サーバ側ログと突き合わせる必要が出たときに限り、Mac/Windows と
  DGX の NTP 同期を確認する。
- **超過時の打ち手**（試す順）:
  1. Riva NIM の `endpointing_config` チューニング (DGX 上)
  2. `NIM_TAGS_SELECTOR="mode=str-thr"` で str-thr プロファイル比較 (DGX 上)
  3. クライアント overlay 表示戦略（録音中ステータスで体感を補う）

---

## 5. 進め方

骨組みフェーズと同じく、**各小段階の終わりに止まって承認を待つ**。各小段階終了時の
報告内容:

1. 変更ファイル一覧（`git status`）
2. 動作確認の結果（テストログ / wscat ログ / レイテンシ計測値）。
   段階6-3 以降は Mac/Windows 実機での動作観測を含み、Raiha が **テキスト
   コピペを基本**で Claude に運ぶ。視覚確認が要る場合のみスクショを追加する。
3. 契約テスト `just test-shared` 全件通過の確認
4. 次の小段階への申し送り事項（見えた問題 / 判断保留 / 次の手元作業）

承認後に commit、その後次の小段階へ。実装中に schema との食い違いや判断が要る
点が出たら、**推測せず止まって質問**する。

---

## 6. 計画書の改訂

実装中に小段階の境界・順序・スコープが動いたら、本ドキュメントを改訂しながら
進める（骨組みフェーズの `stage6-readiness.md` と同じ運用）。
