# 言語横断のタスクランナー
# 段階2時点では各タスクはプレースホルダ。実装は段階3以降。

# 既定: タスク一覧を表示
default:
    @just --list

# protocol/schema から server / client の型を再生成
codegen:
    @echo "TODO(stage-3+): protocol/schema から Python (Pydantic) と Rust (serde) の型を再生成"

# server を起動
server:
    @echo "TODO(stage-3+): cd server && uv run python -m dictation_gateway"

# client を Tauri dev で起動
client:
    @echo "TODO(stage-3+): cd client && npm run tauri dev"

# server + 共有 protocol の契約テストを通す
# 段階6-3 以降の Tauri アプリ本体 (client/src-tauri/) は Mac/Windows 実機で
# ビルド・実行するため、DGX (Linux) の devenv shell からはテストに含めない。
# (詳細は docs/stage6-implementation-plan.md §1.5)
# `test-shared` という名前は「server + protocol の共有契約部分だけが対象であり、
# Tauri クライアント本体は含まれない」を明示するため。
test-shared:
    cd server && uv run pytest
    cd client && cargo test -p koecast-protocol --tests

# lint (全言語)
lint:
    @echo "TODO(stage-3+): ruff (server) + cargo clippy (client)"

# fmt (全言語)
fmt:
    @echo "TODO(stage-3+): ruff format (server) + cargo fmt (client)"
