# 音声入力システム — モノレポ設計

Riva ベースのリアルタイム音声入力システム（DGX ゲートウェイ ＋ Win/Mac クライアント）
をひとつのリポジトリで開発するための設計。実装着手前の合意ドキュメント。

関連: プロトコル仕様は `protocol/dictation-gateway-protocol-v1.md`（別ドキュメント）。

---

## 1. このモノレポが解くべき課題

クライアントとサーバを1リポジトリに置く最大の理由は **プロトコルの一貫性** にある。
両者は WebSocket メッセージという1つの契約で結ばれており、契約が片方だけ変わると
即座に壊れる。モノレポにすることで、プロトコル変更を1コミット・1 PR で両側に反映でき、
CI で「契約が両側で守られているか」を機械的に検証できる。

したがって本設計の中心は「`protocol/` を単一の真実とし、server と client がそこから
派生する」構造である。

---

## 2. リポジトリ構成

```
voice-input/
├── README.md
├── devenv.nix              # Nix 開発環境(Python+Rust+Node を一括定義)
├── devenv.yaml
├── justfile                # 言語横断のタスクランナー
├── .github/workflows/      # CI
│
├── protocol/               # ★ 単一の真実: 両側がここから派生する
│   ├── dictation-gateway-protocol-v1.md   # 人間向け仕様
│   ├── schema/             # 機械可読スキーマ(JSON Schema)
│   │   ├── client-to-server.schema.json
│   │   └── server-to-client.schema.json
│   ├── examples/           # ゴールデンメッセージ(契約テスト用)
│   │   ├── start.json
│   │   ├── partial.json
│   │   ├── formatted.json
│   │   └── ...
│   └── VERSION             # protocol_version (= 1) の単一定義
│
├── server/                 # Python WebSocket ゲートウェイ
│   ├── pyproject.toml      # uv で管理
│   ├── src/dictation_gateway/
│   │   ├── __main__.py     # エントリポイント
│   │   ├── server.py       # WebSocket サーバ・接続状態機械
│   │   ├── riva_bridge.py  # Riva ストリーミング中継
│   │   ├── llm.py          # LLM 整形クライアント
│   │   ├── protocol.py     # メッセージ型(schema から生成 or 手書き)
│   │   └── config.py       # 設定ロード
│   ├── tests/
│   │   └── test_contract.py    # protocol/examples を検証
│   └── deploy/
│       └── docker-compose.yml  # DGX 上での起動定義
│
└── client/                 # Tauri アプリ(Win/Mac)
    ├── src-tauri/          # Rust コア
    │   ├── Cargo.toml
    │   └── src/
    │       ├── main.rs     # Tauri 起動・トレイ常駐
    │       ├── ws.rs       # WebSocket クライアント・再接続
    │       ├── audio.rs    # マイク取得(cpal)
    │       ├── hotkey.rs   # グローバルホットキー(global-hotkey)
    │       ├── inject.rs   # テキスト注入(クリップボード経由)
    │       ├── overlay.rs  # partial 表示オーバーレイ
    │       ├── protocol.rs # メッセージ型(schema から生成 or 手書き)
    │       └── config.rs   # 設定ロード
    ├── src/                # フロントエンド(Svelte: 設定画面・オーバーレイUI)
    ├── package.json
    └── tauri.conf.json
```

トップレベルは「契約(`protocol/`)・サーバ・クライアント」の3つだけに保つ。

---

## 3. プロトコルを真実とする仕組み

`protocol/` をどう両側に効かせるか。3層の防御で考える。

### 3.1 人間向け仕様（canonical）

`dictation-gateway-protocol-v1.md` が最終的な正。設計判断や意図はここに書く。

### 3.2 機械可読スキーマ（JSON Schema）

`protocol/schema/*.json` に全メッセージの JSON Schema を置く。これを型生成と
バリデーションの両方に使う。`protocol/VERSION` に `protocol_version` を1箇所だけ定義し、
server / client 双方がビルド時に読む（数値の二重管理を防ぐ）。

### 3.3 型生成（推奨だが任意）

スキーマから両言語の型を生成し、`just codegen` で再生成する。

- Python: `datamodel-code-generator` → `server/src/dictation_gateway/protocol.py`(Pydantic)
- Rust: `typify` → `client/src-tauri/src/protocol.rs`(serde)

生成物はコミットする（CI でビルドできるように）。生成が重ければ、ソロ開発では
**手書きでもよい** — その場合は次のゴールデンテストが drift の唯一の防波堤になる。

### 3.4 ゴールデンメッセージによる契約テスト（必須）

`protocol/examples/` に各メッセージ型の実例 JSON を置く。これを **server と client の
両方のテストが読み込み**、自分の型でパース・再シリアライズできることを検証する。

- `server/tests/test_contract.py` — 各 example を Pydantic でパース
- `client/src-tauri` のテスト — 各 example を serde でデシリアライズ

型生成を省いた場合でも、このゴールデンテストがあれば「片側だけ仕様から外れた」状態を
CI が検出できる。コストが低く効果が高いので、ここは省略しない。

---

## 4. サーバパッケージ設計

- **言語/管理**: Python、`uv`（Raiha の既存ワークフローに合わせる）。
- **責務分割**: `server.py` は WebSocket と接続状態機械のみ。Riva 通信は
  `riva_bridge.py`、LLM は `llm.py` に隔離する。これにより STT/LLM の差し替え
  （Riva→Whisper など、未確定リスクへの保険）が `server.py` を触らず可能。
- **設定**: `config.py` が環境変数 + 設定ファイルを読む。Riva アドレス、LLM
  エンドポイント、Word Boosting 辞書、システムプロンプト、バインドアドレス
  （tailnet インターフェース）。
- **デプロイ**: `server/deploy/docker-compose.yml` で DGX 上に起動。前回作成した
  vLLM/Whisper の Compose と同じ流儀。Riva 本体は別 Compose または NVIDIA の
  quickstart で起動し、ゲートウェイはそこへ接続する。

---

## 5. クライアントパッケージ設計

- **フレームワーク**: Tauri。`src-tauri/`(Rust コア) と `src/`(Svelte フロント) の
  2部構成。1コードベースで `.app` と `.exe` を生成する。
- **Rust 側の責務**: OS 深部に触れる処理に限定する。
  - `ws.rs`: ゲートウェイへの WebSocket 接続・再接続(指数バックオフ)
  - `audio.rs`: `cpal` でマイク取得し PCM チャンクを送出
  - `hotkey.rs`: `global-hotkey` でプッシュトゥトーク
  - `inject.rs`: クリップボード退避→ペースト→復元(`enigo` / `arboard`)
  - `overlay.rs`: 枠なし・常時最前面ウィンドウで partial を表示
- **フロント側の責務**: 設定画面と、オーバーレイの見た目。ロジックは持たない。
- **設定**: `config.rs` が OS 標準ディレクトリ(`platformdirs` 相当)から読む。
  接続先は Tailscale MagicDNS 名 1つ。
- **注意**: `enigo` 等で足りない注入処理（特に Windows のフォアグラウンド制御、
  macOS アクセシビリティ）は Rust/OS ネイティブを書く前提（OpenWhispr も Swift/C
  ヘルパーを抱えている — フレームワーク選択では消えない領域）。

---

## 6. 開発環境とツールチェイン

モノレポは Python・Rust・Node の3トーチェーンを抱える。これを役割分担で管理する:
**Python は `uv` が、Rust・Node・`just` は `devenv`/Nix が**それぞれ担う。

- **`devenv.nix`**: Rust(cargo)・Node・`just`・Tauri のシステム依存（WebKitGTK 等、
  実機ビルド検証時に追記）を宣言。`devenv shell` で全員（＝Raiha）が同じ環境。
- **Python は `uv` が管理**: `server/pyproject.toml` の `requires-python` に従って
  Python ランタイム自体も `uv` が自前調達する。`devenv.nix` には Python を含めない
  （上流の Python ビルド不整合を避け、言語専用マネージャに責務を集約する方針）。
- **`justfile`**: 言語横断のタスクを集約。
  - `just codegen` — schema から型を再生成
  - `just server` / `just client` — それぞれ起動
  - `just test` — server・client・契約テストを通す
  - `just lint` / `just fmt`
- 各言語のネイティブツール（`uv`, `cargo`, `npm`）はそのまま使い、`just` は
  それらを薄く束ねるだけにする（独自ビルドシステムを作らない）。

---

## 7. ビルドとリリース

- **サーバ**: Docker イメージとしてビルドし、DGX へ。タグはリポジトリのバージョンと
  揃える。
- **クライアント**: Tauri が `.dmg`(Mac) / `.msi` or `.exe`(Windows) を生成。
  GitHub Actions の matrix で macOS ランナーと Windows ランナーの両方でビルド。
- **署名**: Raiha 個人利用のため当面は省略可（Mac は初回 quarantine 解除、
  Windows は SmartScreen を手動で通す）。
- **バージョン整合**: server と client のリリースは同一タグから出す。`protocol`
  に破壊的変更が入る場合は `protocol_version` を上げ、両側を同時にリリースする。

---

## 8. CI（GitHub Actions）

PR ごとに以下を回す。

| ジョブ | 内容 |
|---|---|
| `protocol` | JSON Schema 自体の妥当性、`examples` がスキーマに適合するか |
| `server` | lint・型チェック・`test_contract.py`（examples をパース） |
| `client` | `cargo test`（examples を serde でパース）・`cargo clippy` |
| `codegen-drift` | `just codegen` を実行し差分が出ないことを確認（型生成を使う場合） |

`protocol/examples` を server と client の両ジョブが参照するため、契約違反は
必ずどちらかのジョブで落ちる。これがモノレポ構成の主目的。

---

## 9. バージョニングと運用方針

- リポジトリ全体で単一のバージョン番号。`protocol/VERSION` の `protocol_version`
  はそれとは独立で、プロトコル破壊的変更時のみ上がる。
- ブランチは `main` 一本＋作業ブランチ（ソロ開発のため軽量に）。
- プロトコルを変える PR は、必ず `protocol/`・`server/`・`client/` の3箇所を
  同一 PR で更新する（モノレポにした意味がここで効く）。

---

## 10. 未確定事項（実装前に決める）

- **Riva の DGX Spark(ARM64/GB10) 対応**と日本語ストリーミングモデルの有無。
  不可なら server を Whisper バッチに縮退し、`protocol` の `partial` を簡易版に
  落とす（プロトコル仕様 v1 の未確定事項と同じ）。
- Tauri が要求する OS 依存（WebView2 / WebKitGTK 等）の `devenv.nix` への記述。
  実機ビルド検証に着手する段階で追記する。

---

## 11. 確定事項（段階1〜4で決まったもの）

- **型生成は採用せず、手書き＋ゴールデンテスト方式で運用**（3.3 ではなく 3.4 のみ）。
  Pydantic v2 + jsonschema による契約テストで drift を検出する。drift が頻発する
  兆候が出たら `just codegen`（3.3）の導入を再検討する。
- **フロントエンドフレームワークは Svelte**（`client/package.json` 参照）。
- **Tauri は 2.x**（`client/src-tauri/Cargo.toml` / `client/tauri.conf.json` 参照）。
- **言語ランタイム**: Python 3.14（`uv` が管理、`devenv.nix` 対象外）/
  Rust stable / Node 24 LTS / just（後3つは `devenv.nix` で宣言）。
- **プロトコル契約テストは server 側 (Pydantic + jsonschema) を先行整備済み**。
  client (serde) は段階5以降で追加する。
