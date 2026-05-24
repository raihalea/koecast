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
として残してある項目を実機で潰す。各項目を Yes/No で確定させる。

### 2.1 サーバ起動

- [ ] **Riva server が DGX Spark (ARM64/GB10) で起動するか**
  - NVIDIA NGC で配布されている Riva コンテナの ARM64 ビルドがあるか
  - GB10 (Blackwell 派生) の GPU で CUDA / cuDNN / TensorRT バージョンが整合するか
  - quickstart の `riva_start.sh` 相当が完走するか
- [ ] **NGC からのモデルダウンロードが ARM64 で通るか**
  - `riva_init.sh` の `nemo2riva` / `riva-build` ステップ

### 2.2 日本語ストリーミング ASR モデル

- [ ] **Riva に日本語ストリーミング ASR モデルが提供されているか**（NGC カタログ要確認）
  - 無い場合: NeMo 経由で自前 finetune が現実的か
- [ ] **Word Boosting が日本語で機能するか**
  - `dictation-gateway-protocol-v1.md` §4.1 の `context` フィールドの前提
  - SentencePiece / BPE 語彙との相性

### 2.3 partial / final の挙動

- [ ] **`partial`（interim result）が連続で返るか**
  - `dictation-gateway-protocol-v1.md` §5.2 の置換セマンティクス（同一 segment_id の
    partial は前の partial を置換）どおりに動くか
- [ ] **`final`（endpoint 検出による確定）が想定どおり返るか**
  - `dictation-gateway-protocol-v1.md` §5.3、segment_id が単調増加する整数で返るか
- [ ] **`final` に句読点が付与されるか**
  - `dictation-gateway-protocol-v1.md` §9 の「句読点付与を Riva 側で行う前提」を確定

### 2.4 レイテンシ

- [ ] **partial の出現遅延が体感許容範囲か**
  - チャンク長 100 ms（`dictation-gateway-protocol-v1.md` §3）の送信から partial 返却まで
- [ ] **stop → final → formatted までの遅延**
  - LLM 整形を挟む段階を含む end-to-end

### 2.5 簡易負荷（Raiha 個人利用想定）

- [ ] **1 セッションで GPU メモリ / VRAM が破綻しないか**
- [ ] **常駐 + 1 同時接続で十分余裕があるか**

---

## 3. 検証結果「動く」の場合 — 設計どおり進める段階6

`monorepo-design.md` §4・§5 の責務分割に従って実装本体に着手する。

### 3.1 server 側
- `server.py`: WebSocket サーバと接続状態機械
  （`dictation-gateway-protocol-v1.md` §2 のステート図を実装）
- `riva_bridge.py`: Riva ストリーミング中継
- `llm.py`: LLM 整形クライアント（失敗時 `formatted.fallback: true` で `final` 原文を返す）
- `config.py`: Riva アドレス / LLM エンドポイント / 辞書 / バインドアドレス
- `__main__.py`: エントリポイント
- `deploy/docker-compose.yml`: DGX 上での起動定義

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

- [ ] §2 のチェックリストの結果が「動く」or「動かない」で確定
- [ ] §3（動く）または §4（動かない）どちらの進路で進めるか合意
- [ ] §5 の protocol クレート分離を段階6 冒頭でやるかの判断

---

骨組みフェーズはここで閉じる。Riva 実機検証の進め方は別途相談する。
