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
- `just test`（pytest 22 + cargo test 7）が緑のまま維持

この状態に到達するまで段階6-3 には入らない。

### 1.3 小段階の粒度

骨組みフェーズと同程度の粒度（1日〜数日）。各小段階で「動く形」になることを
保証し、終了時に止まって確認を待つ。

### 1.4 契約テストの維持

全小段階で `just test`（server pytest 22 件 + client cargo test 7 件）を
**緑のまま維持**。実装で型を増やすなら `protocol/schema/` / `protocol/examples/` /
Pydantic / serde を**同一コミット**で更新する（`monorepo-design.md` §9 の方針）。

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
- `just test` 緑

を満たしたら段階6-3 へ進む。満たさなければ満たすまで段階6-2 を継続する。

---

### 2.2 段階6-3: クライアント実装本体 (`client/src-tauri/`)

#### 段階6-3-a: tauri 復活 + `devenv.nix` の OS 依存追記 + 起動確認

- **実装**:
  - `client/src-tauri/Cargo.toml`: `tauri` / `tauri-build` を復活
    （Cargo.toml 内のコメントに従って `[build-dependencies]` / `[dependencies]`）
  - `client/src-tauri/build.rs`: 標準の `tauri_build::build()` 1 行
  - `devenv.nix`: Tauri 必須の OS 依存を追加
    （webkitgtk_4_1 / libsoup_3 / dbus / libayatana-appindicator /
    openssl / pkg-config 等）
  - `client/src-tauri/src/main.rs`: 最小の Tauri 起動（空ウィンドウ）
  - `client/tauri.conf.json` の基本確認
- **動作確認**:
  - `devenv shell`（新 OS 依存を取得）
  - `cd client && npm install`
  - `cd client && npm run tauri dev` で空ウィンドウ起動
  - `just test` 緑維持（段階6-1 までのテストを継承）
- **手元作業**: なし（devenv が新規パッケージを引いてくる）

#### 段階6-3-b: `ws.rs` + 接続状態機械 + `config.rs` + 最低限の Svelte 表示

- **実装**:
  - `client/src-tauri/src/ws.rs`: `tokio-tungstenite` で WebSocket 接続、
    指数バックオフ再接続、`ready`/`partial`/`final`/`formatted`/`session_end`/
    `error` 受信
  - `client/src-tauri/src/config.rs`: 設定ファイル読み
    （サーバ URL / ホットキー / 辞書）
  - `client/src/App.svelte`: サーバ URL 表示 + 接続状態表示の最小 UI
  - Rust ↔ Svelte は Tauri command で
- **動作確認**:
  - サーバを起動した状態で Tauri アプリ起動 → "READY" 表示
  - サーバを止めて再起動 → 指数バックオフで自動再接続
- **手元作業**: なし（サーバが立っていれば良い）

#### 段階6-3-c: `audio.rs` + `hotkey.rs` ★first partial レイテンシ再測定

- **実装**:
  - `client/src-tauri/src/audio.rs`: `cpal` でマイク取得、16kHz mono PCM16 に変換、
    100 ms チャンクで PCM を `ws.rs` に渡す
  - `client/src-tauri/src/hotkey.rs`: `global-hotkey` でプッシュトゥトーク
    （押下: `start` + 録音開始、解放: `stop` + 録音停止）
- **動作確認**:
  - サーバ + Riva NIM 起動状態でアプリ起動
  - ホットキー押下 → 発話 → 解放
  - サーバの partial/final ログを観察、（まだ overlay 無いので）Tauri アプリの
    状態表示で確認
- **★first partial レイテンシ再測定**:
  - 計測方法: ホットキー押下時刻 → 最初の `partial` 受信時刻の差分
    （`ws.rs` / `audio.rs` に時刻取得・ログ出力を仕込む）
  - 比較対象: 検証フェーズで観測した **2.84 s（ファイル投入）**
  - **超過の基準値（許容ライン）は 6-3-c 到達時に実機の体感で決める**。
    数字を事前に決めうちせず、実機マイクで自分の発話 → 表示までの感覚を見て
    「これは遅い／これは許容」を判定する
  - 超過時の打ち手（試す順）:
    1. Riva NIM の `endpointing_config` チューニング
    2. `NIM_TAGS_SELECTOR="mode=str-thr"` で str-thr プロファイル比較
    3. クライアント overlay 表示戦略（録音中ステータスで体感を補う）
- **手元作業**: Riva NIM 起動（6-2-b の compose をそのまま使用）

#### 段階6-3-d: `overlay.rs`（partial 表示）

- **実装**:
  - `client/src-tauri/src/overlay.rs`: 枠なし最前面の別ウィンドウ
  - Svelte 側に overlay 用ルートを作り、`ws.rs` から受けた partial/final/formatted を
    逐次表示
- **動作確認**:
  - 発話中にオーバーレイに partial がリアルタイム表示
  - segment 境界で `final` / `formatted` に置き換わる
- **手元作業**: なし

#### 段階6-3-e: `inject.rs`（クリップボード退避 → ペースト → 復元）

- **実装**:
  - `client/src-tauri/src/inject.rs`: `arboard` でクリップボード退避、
    `formatted` テキストを書き込み、`enigo` で Ctrl+V キーストローク送信、
    退避内容を復元
- **動作確認**:
  - メモ帳 / VSCode / Slack 等でカーソルを合わせて発話 → 確定テキストが
    ペーストされる
  - クリップボードに元入っていた内容が復元されている
- **手元作業**: LLM 起動（`formatted` 経路まで通すため、6-2-c で起動した
  qwen36-mtp をそのまま使用）

#### 段階6-3-f: Svelte 設定画面の充実

- **実装**: サーバアドレス / ホットキー設定 / 用語辞書 / システムプロンプト
  編集 UI
- **動作確認**: 設定変更 → 再接続 → 反映
- **手元作業**: なし

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

---

## 3. Riva NIM / LLM コンテナ起動が必要なタイミング

| 段階 | Riva NIM | LLM (qwen36-mtp) |
|---|:---:|:---:|
| 6-2-a | — | — |
| **6-2-b** | **★初回起動**（docker login + mkdir nim-cache + NGC_API_KEY） | — |
| **6-2-c** | 継続 | **★起動**（既存 compose 経由） |
| 6-2-d | 継続 | 継続 |
| 6-3-a | — | — |
| 6-3-b | — (サーバさえ立っていれば良い) | — |
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

- **位置**: 段階6-3-c（`audio.rs` + `hotkey.rs` 実装直後）
- **方法**: ホットキー押下時刻 → 最初の `partial` 受信時刻の差分を計測
- **比較対象**: 検証フェーズで観測した **2.84 s（ファイル投入）**
- **許容ラインの決定**: **6-3-c 到達時に実機の体感で決める**。数字を事前に
  決めうちせず、実機マイクで自分の発話 → 表示までの感覚を見て「これは遅い／
  これは許容」を判定する
- **超過時の打ち手**（試す順）:
  1. Riva NIM の `endpointing_config` チューニング
  2. `NIM_TAGS_SELECTOR="mode=str-thr"` で str-thr プロファイル比較
  3. クライアント overlay 表示戦略（録音中ステータスで体感を補う）

---

## 5. 進め方

骨組みフェーズと同じく、**各小段階の終わりに止まって承認を待つ**。各小段階終了時の
報告内容:

1. 変更ファイル一覧（`git status`）
2. 動作確認の結果（テストログ / wscat ログ / スクショ / レイテンシ計測値）
3. 契約テスト `just test` 全件通過の確認
4. 次の小段階への申し送り事項（見えた問題 / 判断保留 / 次の手元作業）

承認後に commit、その後次の小段階へ。実装中に schema との食い違いや判断が要る
点が出たら、**推測せず止まって質問**する。

---

## 6. 計画書の改訂

実装中に小段階の境界・順序・スコープが動いたら、本ドキュメントを改訂しながら
進める（骨組みフェーズの `stage6-readiness.md` と同じ運用）。
