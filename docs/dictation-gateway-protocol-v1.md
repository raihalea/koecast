# Dictation Gateway — WebSocket プロトコル仕様 v1

DGX Spark 上の dictation ゲートウェイと、Windows/Mac クライアントの間の通信仕様。
クライアントはこのゲートウェイとのみ通信し、Riva・LLM とは直接やり取りしない。

---

## 1. トランスポート

- **方式**: WebSocket（単一接続を常時維持。発話のたびに張り直さない）
- **URL**: `ws://<dgx-tailscale-name>:<port>/v1/dictation`
- **接続経路**: Tailscale。暗号化・認証は WireGuard に委ね、プロトコル層では扱わない
- **フレーム種別の使い分け**:
  - **Binary フレーム** = 音声データ（PCM）のみ
  - **Text フレーム** = 制御メッセージ・イベント（すべて JSON、UTF-8）
  - WebSocket が Binary/Text を区別するため、音声と制御を見分けるための独自ヘッダは不要

JSON メッセージは必ず `type` フィールドを持つ。クライアントは未知の `type` を無視してよい（前方互換）。

- `type` フィールドの値は各メッセージ仕様に書かれた文字列リテラルに固定する（schema 上は `const`）。
- メッセージ内の未知のフィールドは許可しない（schema 上は全 object で `additionalProperties: false`）。プロトコル拡張は新しい `type` の追加または `protocol_version` の更新で行う。

---

## 2. 接続ライフサイクルと状態遷移

```
[CONNECTING] --(WS確立)--> サーバが ready を送信 --> [READY]
[READY]      --(client: start)----------------------> [LISTENING]
[LISTENING]  --(client: 音声フレーム送信, サーバ: partial/final/formatted)--> [LISTENING]
[LISTENING]  --(client: stop)--> サーバが残りを flush --> session_end --> [READY]
[*]          --(error: recoverable=false / WS切断)----> [CLOSED] → 再接続
```

- 1接続の中で `start` → `stop` を何度でも繰り返せる（プッシュトゥトークの1押下 = 1サイクル）。
- `start` から `stop` までの区間を **セッション** と呼ぶ。
- 1セッション内で Riva のエンドポイント検出により複数の **セグメント**（文の区切り）が生まれうる。

---

## 3. 音声フォーマット

Binary フレームで送る PCM は以下に固定（`start` で上書き可能、下記はデフォルト）。

| 項目 | 値 |
|---|---|
| エンコーディング | 符号付き 16-bit リトルエンディアン PCM (LINEAR_PCM) |
| サンプルレート | 16000 Hz |
| チャンネル | 1 (モノラル) |
| 推奨チャンク長 | 100 ms = 1600 サンプル = 3200 バイト |

- Binary フレームはヘッダを持たない生 PCM。フォーマットは `start` の合意で確定済みとする。
- 順序保証は WebSocket(TCP) に依存するため、シーケンス番号は付けない。

---

## 4. メッセージ catalog — クライアント → サーバ

### 4.1 `start`（Text）

セッション開始。ホットキー押下時に送る。

```json
{
  "type": "start",
  "protocol_version": 1,
  "audio": { "sample_rate": 16000, "encoding": "LINEAR_PCM", "channels": 1 },
  "language": "ja-JP",
  "enable_formatting": true,
  "context": ["ThreatLens", "Bedrock"]
}
```

- `type` / `protocol_version` は必須。v1 では `protocol_version` の値は `1` 固定。
- `audio` / `language` / `enable_formatting` / `context` は省略可（サーバのデフォルトを使用）。
- `audio` を指定する場合は `sample_rate` / `encoding` / `channels` の3フィールドすべて必須（部分上書きは不可）。
- `audio.encoding` は `LINEAR_PCM` のみ受理。
- `enable_formatting`: `false` なら LLM 整形をスキップし `final` のみ返す。
- `context`: そのセッション限定で追加する**用語辞書**。サーバ側で **LLM 整形プロンプトに
  渡して用語補正に使う**。常用辞書はサーバ側が保持。
  - 当初は Riva の Word Boosting (`speech_contexts`) に渡す前提だったが、実機検証
    （`stage6-readiness.md` §2.2）で Parakeet 1.1b RNNT Multilingual の ja に対しては
    効果がないことが判明したため、用途を LLM 整形側へ移した。Riva には渡さない。

### 4.2 音声フレーム（Binary）

`start` 送信後、`stop` までの間、PCM チャンクを Binary フレームで連続送信する。

### 4.3 `stop`（Text）

音声入力終了。ホットキー解放時に送る。サーバは Riva に最終化を指示し、残った
partial を `final`/`formatted` に変換しきってから `session_end` を返す。

```json
{ "type": "stop" }
```

### 4.4 `ping`（Text, 任意）

アプリ層キープアライブ。WebSocket 標準の ping/pong で足りる場合は不要。

```json
{ "type": "ping" }
```

---

## 5. メッセージ catalog — サーバ → クライアント

すべて Text(JSON)。`segment_id` は接続内で単調増加する整数で、`final` と `formatted`
を対応づける鍵になる。

### 5.1 `ready`（接続直後）

```json
{
  "type": "ready",
  "protocol_version": 1,
  "server": "dictation-gateway/1.0",
  "defaults": { "sample_rate": 16000, "encoding": "LINEAR_PCM", "language": "ja-JP" }
}
```

- `protocol_version` / `server` / `defaults` はすべて必須。
- `defaults` 内の `sample_rate` / `encoding` / `language` もすべて必須。

### 5.2 `partial`（未確定テキスト）

Riva の interim result。同一 `segment_id` の `partial` は常に直前を**置換**する
（追記ではない）。クライアントはこれをオーバーレイ表示のみに使い、エディタには書かない。

```json
{ "type": "partial", "segment_id": 7, "text": "ベッドロックでLLMを" }
```

`type` / `segment_id` / `text` はすべて必須。

### 5.3 `final`（確定テキスト・整形前）

Riva が文の区切りを確定したもの。情報用（オーバーレイで「確定待ち」表示の更新等）。
注入には使わない。

```json
{ "type": "final", "segment_id": 7, "text": "ベッドロックでLLMを動かす" }
```

`type` / `segment_id` / `text` はすべて必須。

### 5.4 `formatted`（整形済みテキスト・注入対象）

LLM 整形の結果。**クライアントはこれだけをアクティブウィンドウへ注入する。**
`final` と同じ `segment_id` を持つ。

```json
{
  "type": "formatted",
  "segment_id": 7,
  "text": "Bedrock で LLM を動かす",
  "fallback": false
}
```

- `type` / `segment_id` / `text` / `fallback` はすべて必須。
- `fallback: true` の場合、LLM 整形が失敗し `text` は `final` の原文そのまま。
  入力を失わないための仕様（要件 3 の「失敗時は原文を返す」に対応）。
- `enable_formatting: false` のセッションでは `formatted` は送られない。

### 5.5 `session_end`

`stop` 後、そのセッションのすべての `final`/`formatted` を送り終えたことを示す。
受信後、クライアントは状態を READY に戻す。

```json
{ "type": "session_end", "segments": 3 }
```

`type` / `segments` はすべて必須。

### 5.6 `error`

```json
{
  "type": "error",
  "code": "RIVA_UNAVAILABLE",
  "message": "Riva backend not reachable",
  "recoverable": false
}
```

- `type` / `code` / `message` / `recoverable` は必須。
- `segment_id` は省略可。セグメントに紐づくエラー（例: `RIVA_STREAM_ERROR` が特定セグメント中に発生）でのみ含める。接続全体に関するエラー（`RIVA_UNAVAILABLE` / `UNSUPPORTED_VERSION` / `INTERNAL` 等）では省略する（`null` 値ではなくフィールドごと省く）。
- `recoverable: false` の場合、クライアントは接続を破棄して再接続する。

---

## 6. シーケンス例（プッシュトゥトーク 1 回）

```
クライアント                           ゲートウェイ
    | --- WS接続 ------------------------> |
    | <-- {ready} ------------------------ |
    |     [ホットキー押下]                  |
    | --- {start} -----------------------> |   Riva ストリーム開始
    | --- 音声(Binary) ------------------> |
    | --- 音声(Binary) ------------------> |
    | <-- {partial seg=7 "ベッドロック"} -- |   → オーバーレイ更新
    | --- 音声(Binary) ------------------> |
    | <-- {partial seg=7 "ベッドロックで"} | → オーバーレイ更新(置換)
    |     [ホットキー解放]                  |
    | --- {stop} ------------------------> |   Riva 最終化
    | <-- {final seg=7 "...動かす"} ------- |
    | <-- {formatted seg=7 "Bedrock で..."}|   → エディタへ注入
    | <-- {session_end segments=1} -------- |   → 状態 READY へ
```

---

## 7. エラーコード一覧

| code | 意味 | recoverable |
|---|---|---|
| `BAD_MESSAGE` | 不正な JSON / 必須フィールド欠落 | true |
| `UNSUPPORTED_VERSION` | `protocol_version` 不一致 | false |
| `AUDIO_FORMAT_REJECTED` | `start` の音声指定を受理できない | true |
| `RIVA_UNAVAILABLE` | Riva バックエンドに接続不可 | false |
| `RIVA_STREAM_ERROR` | ストリーミング中に Riva が異常終了 | false |
| `LLM_UNAVAILABLE` | LLM に接続不可（※ `formatted` は fallback で継続） | true |
| `INTERNAL` | ゲートウェイ内部エラー | false |

`LLM_UNAVAILABLE` はセッションを止めず、`formatted` を `fallback: true` で返し続ける。

---

## 8. キープアライブと再接続

- **キープアライブ**: WebSocket 標準の ping/pong（RFC 6455）で十分。Tailscale 越しの
  アイドル切断対策として、ゲートウェイは 30 秒間隔で ping を送る。
- **再接続**: クライアントは WS 切断 / `recoverable: false` を検知したら、指数
  バックオフ（1s, 2s, 4s, ... 上限 30s）で再接続する。再接続後は `ready` を待ち
  READY から再開。`segment_id` は接続ごとにリセットされる。

---

## 9. バージョニングと未確定事項

- `protocol_version` を `start` と `ready` の双方に含める。不一致時は
  `UNSUPPORTED_VERSION` で接続を閉じる。本書は v1。
- v1 では `protocol_version` の値は厳密に `1`（整数）。schema 上は `const: 1` で固定する。
- 機械可読スキーマは `protocol/schema/client-to-server.schema.json` と
  `protocol/schema/server-to-client.schema.json`、契約テスト用のゴールデン例は
  `protocol/examples/*.json`、`protocol_version` の数値定義は `protocol/VERSION` を
  単一の真実とする。本書（人間向け仕様）とこれらが食い違ったら、両者を揃える PR を
  通すまで実装に進まない。
- **確定済み（実装前提）** — `stage6-readiness.md` §2 の実機検証で確定:
  - STT バックエンドは **Riva ASR NIM** (`parakeet-1-1b-rnnt-multilingual:1.4.0`、
    `NIM_TAGS_SELECTOR="mode=str"`)。DGX Spark / Blackwell 公式サポート、
    ja-JP 対応、ストリーミング interim result 対応。
  - 句読点付与は Riva 側で `enable_automatic_punctuation=true` を指定して行う。
    LLM 側はフィラー除去と用語補正に専念する。
  - `segment_id` は server (`riva_bridge.py`) 側で生成する。Riva の
    `StreamingRecognitionResult` 自体には segment_id 相当のフィールドが無いため、
    接続開始時 0、`is_final=True` ごとにインクリメント、再接続でリセット（§8 と整合）。
  - ja 文字間空白は server 側で除去する。Riva は ja 出力に 1 字ごとに半角空白を
    入れて返すが、`partial` / `final` の `text` フィールドはクライアントから見て
    空白除去済みであることを本仕様が保証する。
  - `context` フィールド（§4.1）は LLM 整形へ渡す用語辞書として使う。Riva の
    Word Boosting は ja に対して機能しないため Riva 側には渡さない。

- **未確定（実装中に判断する）**:
  - first partial の真のレイテンシ。ファイル投入での観測は 2.84 s だが、これが
    モデルの内部遅延か発音タイミング由来かは段階6 でクライアントの音声入力
    （`audio.rs` + `hotkey.rs`）実装後にマイクからの実機計測で切り分ける。
    許容できなければ Riva の `endpointing_config` チューニング、`mode=str-thr` 比較、
    または overlay 表示戦略で調整。
  - 句読点（特に読点「、」）の ja 品質。長期使用での精度は段階6 実装後に体感ベースで
    再評価。低ければ LLM 整形側で句読点を入れ直す戦略に切り替え。
  - チャンク長 100ms は初期値。遅延と安定性を見て調整する。
```
