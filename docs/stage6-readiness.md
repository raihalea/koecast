# Stage 6 Readiness — Riva 実機検証と段階6 着手前の整理

骨組みフェーズ（段階1〜5）が完了し、protocol/ の契約が server (Pydantic) と
client (serde) の両側で機械的に固まった。段階6（実装本体）に進む前に、
何度も「未確定の最大リスク」として残してきた **Riva の DGX Spark 実機検証** を
潰す必要がある。本ドキュメントはその検証スコープと、結果に応じた段階6 の進路、
および段階6 冒頭で判断する protocol クレート分離をまとめる。

関連ドキュメント:

- `monorepo-design.md` — リポジトリ全体設計（§10 未確定事項 / §11 確定事項）
- `dictation-gateway-protocol-v1.md` — プロトコル仕様 v1（§5 メッセージ catalog / §9 未確定事項）

---

## 1. 現在地（段階1〜5 で固まったもの）

- `protocol/`: schema (`client-to-server.schema.json` / `server-to-client.schema.json`)、
  examples 11件、`VERSION` (= 1)。
- server (Pydantic v2): `server/src/dictation_gateway/protocol.py` + 契約テスト
  (22件、`just test` で通過)。
- client (serde): `client/src-tauri/src/protocol.rs` + 契約テスト
  (7件、negative ケース込み、`just test` で通過)。
- 設計ドキュメント: §11 で「確定事項」として型生成方針・Svelte・Tauri 2.x・
  言語ランタイム・契約テスト先行整備を明記。
- devenv: Rust / Node / just を `devenv.nix` で、Python は `uv` が `requires-python`
  に基づいて自前調達。

未実装で骨組みフェーズに含めなかったもの:

- 実装本体（server.py、riva_bridge.py、llm.py、ws.rs、audio.rs、hotkey.rs、
  inject.rs、overlay.rs、フロントエンド Svelte 一式）
- Tauri OS 依存（WebKitGTK / libsoup / dbus 等）の `devenv.nix` 追記
- Tauri / tauri-build を `client/src-tauri/Cargo.toml` から意図的に外したまま

---

## 2. Riva 実機検証チェックリスト

`monorepo-design.md` §10 と `dictation-gateway-protocol-v1.md` §9 で「未確定」
として残してある項目を実機で潰す。**検証フェーズは完了**（2026-05-25, GB10 実機で
`nvcr.io/nim/nvidia/parakeet-1-1b-rnnt-multilingual:1.4.0` を起動して観測）。

結論: **動く（§3 の進路で進める）**。ただし以下 2 点は client (`riva_bridge.py`) 側で
補完する必要があり、それを反映した実装責務は §3.1 を参照。

- ja 文字間空白除去（モデルは `ベ ッ ド ロ ッ ク` のように 1 字ごとに空白を入れて返す）
- Word Boosting は ja に対して機能しない → 用語補正は LLM 整形側へ移譲

### 2.0 Blackwell 固有の前提条件（実機検証時に厳守）

- **イメージタグは `:1.4.0` 固定**。`:1.5.0` / `:latest` は使用禁止。
  - 理由: 1.5.0 で sortformer (diarizer) の TensorRT エンジンビルドが Blackwell
    (compute 12.x) で失敗する既知バグあり。
    ASR エンコーダ本体は Blackwell で動作することが NVIDIA 開発者フォーラムで確認済み
    （forum 363309）。
- 起動時環境変数 **`NIM_TAGS_SELECTOR="mode=str"`** を指定。
  - 1.4.0 では DGX Spark 専用の prebuilt プロファイル
    `gpu=dgx_spark, diarizer=disabled, mode=str, vad=default, model_type=prebuilt`
    が自動選択される（実機で確認）。`mode=str` 単体で profile が一意決定する。
- 将来 1.6.0 以降で Blackwell 対応 diarizer プロファイルが復活する可能性があるが、
  koecast はそもそも diarizer 不要なので追従不要。
- **NIM キャッシュの bind mount は host で事前 mkdir が必要**。
  image に `/opt/nim/.cache` が存在しないため、bind 先・named volume いずれでも
  docker が root 所有で初期化し、container 内の `riva-server` (uid 1000) が
  書けず `Permission denied (os error 13)` で起動失敗する。
  host UID 1000 で `mkdir nim-cache` してから `docker compose up` すること。

#### Riva NIM 起動 compose のひな形（段階6 で `server/deploy/` 配下に書く時の起点）

検証フェーズで動作確認したものをそのまま貼れる形で残しておく。NGC API key 発行
手順 → `docker login nvcr.io`（Username: `$oauthtoken`、Password: NGC API key）→
host で `mkdir nim-cache` → `export NGC_API_KEY=<key>` → `docker compose up` で起動。

```yaml
services:
  parakeet:
    image: nvcr.io/nim/nvidia/parakeet-1-1b-rnnt-multilingual:1.4.0
    container_name: parakeet-asr  # 検証時は parakeet-asr-verify
    runtime: nvidia
    shm_size: "8gb"               # NIM 既定要件
    environment:
      NIM_TAGS_SELECTOR: "mode=str"
      NGC_API_KEY: ${NGC_API_KEY:?NGC_API_KEY must be set in host shell}
      NIM_GRPC_API_PORT: "50051"
      NIM_HTTP_API_PORT: "9000"
    ports:
      - "50051:50051"             # Riva 標準 gRPC
      - "9000:9000"               # NIM HTTP (/v1/health/ready 等)
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    volumes:
      - ./nim-cache:/opt/nim/.cache  # host で事前 mkdir 必須（UID 1000）
```

ready 判定は `curl -s localhost:9000/v1/health/ready` が
`{"status":"ready"}` を返すまで待つ（初回はモデル DL で 100 秒前後）。
TensorRT エンジンビルドは DGX Spark 専用 prebuilt が含まれるため発生しない。

### 2.1 サーバ起動

- [x] **配布経路の確定**（実機確認済み）
  - Riva Skills Quick Start ARM64 は L4T/Jetson Thor 専用で DGX Spark では使えない
    （Riva 2.24.0 リリースノートで "The Riva SDK release only supports embedded (L4T)
    platforms" と明記、Jetson Orin は deprecated）。
  - 代わりに **Riva ASR NIM** を使用。`parakeet-1-1b-rnnt-multilingual` が
    公式に "supported on Blackwell and DGX Spark platform" と明記
    （NIM Speech ASR Support Matrix）。
- [x] **NIM コンテナが GB10 上で起動する**（実機確認済み）
  - `nvcr.io/nim/nvidia/parakeet-1-1b-rnnt-multilingual:1.4.0` を `docker compose up`
    で起動、`Riva gRPC Server is READY` ログ確認。
  - 初回はモデル RMIR ダウンロード（NeMo tar 約 1.5 GB、約 100 秒）のみ。
    DGX Spark 専用 prebuilt engine が含まれるため **TensorRT ビルドは走らない**。
- [x] **gRPC エンドポイント (50051) / HTTP (9000) listen**（実機確認済み）
  - `/v1/health/ready` が `{"status":"ready"}` を返す。

### 2.2 日本語ストリーミング ASR モデル

- [x] **モデル選定**（実機確認済み）
  - `parakeet-1-1b-rnnt-multilingual` は ja-JP を含む 25 言語に対応、ストリーミング対応。
  - クライアントから `language_code="ja-JP"` または `"multi"` のいずれを渡しても
    日本語認識自体は機能する。
- [x] **ja 認識動作**（実機確認済み）
  - 実音声「ベッドロックでラムダを増やして…書きたいです」を投入し、partial/final が
    日本語で返ることを確認。
- [x] **ja 文字間空白の挙動**（実機確認済み）❌
  - 1 文字ごとに半角空白が挿入される（`ベ ッ ド ロ ッ ク`）。
  - 公式 docs には「`language_code=ja-JP` を渡せば空白なしになる」とあるが、
    **NIM 1.4.0 では実装されていない**。`language_code="ja-JP"` / `"multi"`、
    `verbatim_transcripts=True/False`、`custom_configuration=*` の全パターンで
    挙動が変わらないことを確認。
  - 対応: **client (`riva_bridge.py`) 側で CJK 文字間の空白を regex 除去**する。
    §3.1 で責務を明記。
- [x] **Word Boosting が ja で機能するか**（実機確認済み）❌
  - `Bedrock,Lambda,DynamoDB,CDK` を boost 対象に投入したが、出力は
    `ベッドロック / ラムダ / ダイナモデビー / オシディ系` のままで boost 効果なし。
  - Riva の `speech_contexts` API はリクエストとして受理されるが、Parakeet RNNT
    Multilingual の ja には作用しない（NeMo 側の GitHub Issues で RNNT context
    biasing は開発中という議論と整合）。
  - 対応: **用語補正は LLM 整形側で実施**。`dictation-gateway-protocol-v1.md`
    §4.1 の `context` フィールドの用途を「Riva の Word Boosting」から
    「LLM 整形の用語辞書」へ再定義（同仕様書で変更済み）。

### 2.3 partial / final の挙動

- [x] **`partial`（interim result）が連続で返る**（実機確認済み）
  - 単一発話で 21 partial 観測、約 300〜400 ms 周期で更新。
  - `dictation-gateway-protocol-v1.md` §5.2 の置換セマンティクス（同一
    `segment_id` の partial は前の partial を置換）は client 側で再構成できる
    （後述の採番ルール参照）。
- [x] **`final`（endpoint 検出による確定）が返る**（実機確認済み）
  - 9.4 秒の単一発話で Riva が **2 segment に endpoint 分割**して
    `is_final=True` を 2 回返した（「…ベッドロックで…書き持ちオシディ系で」と
    「書きたいです。」）。期待どおり。
- [x] **`final` に句読点が付与される**（実機確認済み）
  - 起動ログで `Automatic punctuation is already supported by the ASR model` を確認。
  - `enable_automatic_punctuation=True` 指定下で、partial / final に「。」が
    付与されることを確認（ja の品質は単発検証のため、長期使用での精度は段階6
    実装後に体感ベースで再評価）。
- [x] **`segment_id` の生成方針**（実機確認済み）
  - Riva の `StreamingRecognitionResult` には `segment_id` / `result_id` 相当の
    フィールドが**存在しない**（フィールドは `alternatives` / `is_final` /
    `stability` / `channel_tag` / `audio_processed` / `pipeline_states`）。
  - **`riva_bridge.py` 側で `is_final=True` を観測した回数をカウントして
    `segment_id` を採番する**こと（接続開始時 0、`is_final=True` ごとに
    インクリメント、再接続でリセット — `dictation-gateway-protocol-v1.md` §8 と整合）。

### 2.4 レイテンシ

- [x] **partial の更新間隔**（実機確認済み）
  - 約 300〜400 ms 周期で新しい partial が返る。チャンク送信 100 ms に対し
    モデル内バッチング込みで合理的な値。
- [ ] **first partial の真のレイテンシ**（段階6 で再測定）
  - ファイル投入での観測値は **first partial = 2.84 s**。これが
    「モデルが確信できる partial を出すまでの内部遅延」か、音声先頭の発音タイミング
    に由来するかの切り分けは、ファイル経路では困難。
  - 切り分け方針: 段階6 で client (`audio.rs` + `hotkey.rs`) の音声入力が実装された
    後に、マイクからリアルタイム入力した際の「発話開始 → first partial 表示」遅延を
    実機計測する。許容できなければ Riva の `endpointing_config` チューニング、
    `mode=str-thr` 比較、または client overlay 側の表示戦略で調整。
- [ ] **stop → final → formatted までの遅延**（段階6 で計測）
  - LLM 整形を挟む段階を含む end-to-end。`server.py` + `llm.py` 実装後に計測。

### 2.5 簡易負荷（Raiha 個人利用想定）

- [x] **1 セッションで GPU メモリ / VRAM が破綻しない**（実機確認済み）
  - 推論中の使用量は tritonserver 約 1.9 GiB + python backend 約 7.2 GiB の
    合計 ~9 GiB。GB10 のユニファイドメモリ 121 GB に対し十分余裕あり。
  - `nvidia-smi` は GB10 のユニファイドメモリ特性のため `Memory-Usage Not Supported`
    と表示される（既知）。プロセス単位の表示は機能している。
- [x] **常駐 + 1 同時接続で余裕**（実機確認済み）
  - 1 ストリーミングセッションで上記消費。同時に llama-server や LLM 整形が
    動く構成でも GB10 ユニファイドメモリで吸収できる見込み（段階6 で再確認）。

---

## 3. 検証結果「動く」の場合 — 設計どおり進める段階6

`monorepo-design.md` §4・§5 の責務分割に従って実装本体に着手する。
段階6-2 以降の小段階分割は `docs/stage6-implementation-plan.md` を参照。

### 3.1 server 側
- `server.py`: WebSocket サーバと接続状態機械
  （`dictation-gateway-protocol-v1.md` §2 のステート図を実装）
- `riva_bridge.py`: **Riva ASR NIM への gRPC ストリーミング中継**
  - クライアントは `nvidia-riva-client` パッケージ、エンドポイントは
    `localhost:50051`（同居推奨。別ホストも可とし `config.py` で切替可能にする）
  - リクエスト設定: `language_code="ja-JP"`（または `"multi"`） /
    `enable_automatic_punctuation=True` / `interim_results=True` を必ず指定
  - **§2.2 / §2.3 の検証結果に基づく追加責務**:
    - **CJK 文字間の空白除去**: Riva は ja 出力に 1 字ごとに半角空白を入れて返すため、
      partial / final 両方に対し
      `r'(?<=[぀-ヿ㐀-鿿＀-￯])\s+(?=[぀-ヿ㐀-鿿＀-￯])'`
      を空文字に置換する（英数や記号との境界の空白は保持する）。
    - **`segment_id` の採番**: Riva の `StreamingRecognitionResult` には
      `segment_id` / `result_id` 相当のフィールドが存在しないため、
      接続開始時 0、`is_final=True` を観測したらインクリメント、を client 側で行う。
      再接続でリセット（`dictation-gateway-protocol-v1.md` §8 と整合）。
    - **`context` フィールドの扱い**: `dictation-gateway-protocol-v1.md` §4.1 の
      `context` は **LLM 整形側の用語辞書** として `llm.py` に橋渡しする。
      Riva の Word Boosting (`speech_contexts`) には**渡さない**（ja で機能しないため）。
- `llm.py`: LLM 整形クライアント（失敗時 `formatted.fallback: true` で `final` 原文を返す）。
  `riva_bridge.py` から橋渡しされた `context` 用語辞書を整形プロンプトに織り込む。
- `config.py`: Riva アドレス（既定 `localhost:50051`）/ LLM エンドポイント / 辞書 /
  バインドアドレス
- `__main__.py`: エントリポイント
- `deploy/docker-compose.yml`: **koecast ゲートウェイの起動定義**。Riva ASR NIM 本体は
  別 compose（`nvcr.io/nim/nvidia/parakeet-1-1b-rnnt-multilingual:1.4.0`、
  `NIM_TAGS_SELECTOR="mode=str"`、bind mount 先は host で事前 mkdir 済みであること）
  で起動し、ゲートウェイは gRPC 50051 で接続する

### 3.2 client 側
- `ws.rs`: WebSocket 接続 + 指数バックオフ再接続
- `audio.rs`: cpal でマイク取得 → PCM チャンク送出
- `hotkey.rs`: global-hotkey でプッシュトゥトーク
- `inject.rs`: クリップボード退避 → ペースト → 復元
- `overlay.rs`: 枠なし最前面ウィンドウで partial 表示
- `main.rs`: Tauri 起動 + トレイ常駐
- フロントエンド (Svelte): 設定画面 + オーバーレイ UI

### 3.3 段階6 冒頭で扱う前提作業
- `client/src-tauri/Cargo.toml` に `tauri` / `tauri-build` を復活
- `devenv.nix` に Tauri OS 依存（WebKitGTK / libsoup / dbus 等）を追記
- **protocol クレート分離**（§5 参照）— Tauri 依存を入れ直す前にやる

### 3.4 プロトコル更新
- 仕様書に変更なし（`protocol_version` は 1 のまま）
- examples / schema / Pydantic モデル / serde 型もそのまま

---

## 4. 検証結果「動かない」の場合 — 縮退設計

`monorepo-design.md` §10 と `dictation-gateway-protocol-v1.md` §9 で示している縮退方針:
**Whisper（バッチ）にフォールバックし、`partial` を簡易版に落とす**。

### 4.1 STT バックエンドの差し替え
- Riva → Whisper（バッチ推論）
- ストリーミング ASR が無くなる → interim result（仕様 §5.2 `partial`）は出せない
- VAD（webrtcvad / silero-vad 等）でセグメント分割、各セグメントを Whisper にバッチ投入

### 4.2 プロトコルへの影響
- `partial` を **省略可能化**（または完全廃止）
- `final` は `stop` 後にまとめて返る挙動になる
  → `dictation-gateway-protocol-v1.md` §2 のステート遷移図を更新
- `dictation-gateway-protocol-v1.md` §3 のチャンク長前提は維持可能
  （クライアント → サーバの送信フォーマット自体は変えない）

### 4.3 プロトコルの版番号
判断が必要:
- (a) `protocol_version` を **`2` に上げる**（破壊的変更として扱う）
- (b) v1 のまま **`partial` を任意送信に格下げ**（後方互換のために schema の `oneOf` から消さず、サーバが送らないだけ）

破壊的変更は実利用前ならコストが小さいので (a) を推奨。schema / examples / 両側の型を
同一 PR で更新する（monorepo-design.md §9 の方針どおり）。

### 4.4 server 側設計の縮退
- `riva_bridge.py` → `whisper_bridge.py` に置き換え
- 状態機械が単純化: `LISTENING` 中は録音バッファ蓄積のみ、`stop` で Whisper 推論 →
  `final` (+ LLM 整形 → `formatted`) をまとめて返す
- `dictation-gateway-protocol-v1.md` §4.1 の `audio` フォーマットはそのまま

### 4.5 client 側設計の縮退
- `overlay.rs`: partial が無いので「録音中」ステータスのみ表示
- `ws.rs` / `audio.rs` / `hotkey.rs` / `inject.rs` は構造維持

### 4.6 縮退時に更新が必要なドキュメント
- `monorepo-design.md` §10: Riva 項を「Whisper 縮退決定済み」へ
- `monorepo-design.md` §11: 確定事項に「STT バックエンドは Whisper」を追加
- `dictation-gateway-protocol-v1.md` §2, §5.2, §9: ステート遷移 / partial / 版番号
- `protocol/schema/*.json` と `protocol/examples/*.json`: partial の扱いを修正
- `protocol/VERSION`: (a) を採るなら `2` に
- 両側の型 (`server/protocol.py` / `client/src-tauri/src/protocol.rs`) と契約テスト

---

## 5. 段階6 冒頭で判断する: protocol クレート分離

検証結果がどちらに転んでも、段階6 で実装本体に着手する **前** に判断したい項目。

### 5.1 背景
段階5 で `libdbus-sys` のビルド失敗を契機に判明した構造的ねじれ:

- 現状 `protocol.rs` は `client/src-tauri/` 配下にあり、`src-tauri` クレートは
  Tauri アプリ本体として `tauri` 依存を持つ
- `tauri` 依存を入れると Linux 推移依存に `libdbus-sys` → `dbus-1` システムライブラリが
  ぶら下がる
- 結果として「serde の型を契約テストするだけ」のために Tauri OS 依存一式を
  巻き込む構造になる

段階5 では `tauri` / `tauri-build` を `client/src-tauri/Cargo.toml` から **意図的に**
外して回避済み（Cargo.toml 冒頭コメント参照）。これは一時回避なので、段階6 で `tauri`
を戻す前に構造を直す。

### 5.2 提案構造

```
client/
├── Cargo.toml                # [workspace] members = ["protocol", "src-tauri"]
├── protocol/                 # 純粋な serde 型 (tauri 非依存)
│   ├── Cargo.toml            # serde / serde_json / [dev] jsonschema のみ
│   └── src/lib.rs            # 現 client/src-tauri/src/protocol.rs を移設
└── src-tauri/                # Tauri アプリ本体
    ├── Cargo.toml            # tauri + tauri-build + path = "../protocol"
    └── src/                  # main.rs / lib.rs / ws.rs / audio.rs / ...
```

### 5.3 効果
- 契約テストは `client/protocol/` 単体で `cargo test`。Tauri / WebKitGTK / dbus と
  永久に無関係になる
- `monorepo-design.md` §3「`protocol/` が単一の真実」「プロトコル型がアプリ実装から
  独立しているべき」という思想に最も忠実な構造
- 段階6 で `tauri` を復活させても契約テストが OS 依存を巻き込まない

### 5.4 段階6 冒頭の作業順序案

1. **protocol クレート分離**（client/protocol/ を新設、workspace 化）
2. 分離後も `just test` の client 側が通ることを確認
3. `client/src-tauri/Cargo.toml` に `tauri` / `tauri-build` を復活、
   同時に `devenv.nix` に Tauri OS 依存（WebKitGTK / libsoup / dbus 等）を追記
4. Tauri アプリ本体の実装に着手（§3.2 もしくは §4.5 に従う）

### 5.5 影響範囲
- 段階4 で書いた `server/tests/test_contract.py` は影響なし（server 側は触らない）
- `protocol/` ディレクトリ（リポジトリ直下の schema / examples / VERSION）は触らない
  — 単一の真実はリポジトリ直下のままで、`client/protocol/` クレートはそれを参照する
  Rust 側の実装

---

## 6. 段階6 着手のトリガー

以下がすべて揃ったら段階6 に進む:

- [x] §2 のチェックリストの結果が「動く」or「動かない」で確定
      → **動く**（2026-05-25 実機検証完了）。ただし client 側で
      CJK 空白除去 / `segment_id` 採番 / Word Boosting 回避 を吸収する必要あり（§3.1）。
- [x] §3（動く）または §4（動かない）どちらの進路で進めるか合意
      → **§3 進路**で段階6 に進む。
- [ ] §5 の protocol クレート分離を段階6 冒頭でやるかの判断

---

骨組みフェーズと Riva 実機検証フェーズはここで閉じる。
段階6 の最初の作業は §5 の protocol クレート分離。
