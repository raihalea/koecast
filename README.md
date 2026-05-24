# koecast

Riva ベースのリアルタイム音声入力システム。DGX 上の WebSocket ゲートウェイ (Python) と
Windows/Mac クライアント (Tauri + Svelte) のモノレポ。

詳細は以下を参照:

- `docs/monorepo-design.md` — リポジトリ設計
- `docs/dictation-gateway-protocol-v1.md` — WebSocket プロトコル仕様 v1

## ディレクトリ構成

- `protocol/` — WebSocket プロトコルの単一の真実 (仕様・JSON Schema・examples)
- `server/` — Python WebSocket ゲートウェイ (uv)
- `client/` — Tauri アプリ (Rust + Svelte)
- `docs/` — 設計ドキュメント

## 開発環境

`devenv shell` で Python・Rust・Node・just を有効化。

```sh
devenv shell
just --list   # 利用可能なタスク一覧
```
