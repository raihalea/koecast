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

# server / client / 契約テストを通す
# 段階4時点では server pytest (= 契約テスト) のみ。client cargo test は段階5以降で追加。
test:
    cd server && uv run pytest

# lint (全言語)
lint:
    @echo "TODO(stage-3+): ruff (server) + cargo clippy (client)"

# fmt (全言語)
fmt:
    @echo "TODO(stage-3+): ruff format (server) + cargo fmt (client)"
