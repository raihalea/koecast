"""エントリポイント。

実行:
    uv run python -m dictation_gateway

設定は config.py の load_config() に従って、環境変数・~/.config/koecast/config.toml・
デフォルト の順に解決される。
"""
from __future__ import annotations

from .server import main

if __name__ == "__main__":
    main()
