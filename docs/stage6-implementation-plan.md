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

### 2.3 段階6-4: 結合確認 + ドキュメント整備

#### 段階6-4-a: end-to-end 実用テスト + 体感品質チューニング

- 1日中・実用ベースで使ってバグ出し
- レイテンシ・誤認識・LLM 整形品質・ペースト挙動の最終調整
- 必要に応じて Riva の `endpointing_config` / `mode=str-thr` 切替、LLM プロンプト
  調整、overlay 表示戦略

#### 段階6-4-b: README / 運用手順 / docs/ 更新

- README に Mac/Windows ビルド手順、サーバ起動手順、ホットキー / 設定ファイルの所在
- `docs/stage6-readiness.md` §6 着手トリガーを全 ☑️ に
- 段階6 完了マーク
- 必要なら `docs/operation.md`（日常運用手順）を新設
- **配布前チェック (段階6-3-d の申し送り)**:
  - `tauri.conf.json` の `app.macOSPrivateApi: true` と `Cargo.toml` の
    `tauri` features `"macos-private-api"` は overlay 透過のために有効化している。
    **Mac App Store に提出する場合は private API 利用が拒否されるため両方外す
    必要がある**。本格配布の計画が出た段階で対応する (Raiha 個人利用の
    `.app` 直接配布なら不問)。
  - `src-tauri/Info.plist` の `NSMicrophoneUsageDescription` が入っていることを
    確認 (段階6-3-c 申し送り)。
  - `src-tauri/icons/icon.png` は段階6-3-a 時点で 32×32 透明のプレースホルダ
    を入れているだけ。配布する `.dmg` を作る前に正規アイコン (ICNS / ICO /
    各解像度 PNG) に差し替える。

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
| 6-4-a | 継続（常用） | 継続（常用） |

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
