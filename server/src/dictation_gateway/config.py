"""dictation-gateway の設定ロード。

優先順位: 環境変数 > 設定ファイル > デフォルト。

設定ファイル探索:
  1. 環境変数 KOECAST_CONFIG=<path> が指定されていればそれ
  2. ~/.config/koecast/config.toml が存在すればそれ
  3. なければデフォルト値のみ

設定ファイルは TOML。フラットなキーで Config の dataclass フィールドに対応する:

  bind_host = "0.0.0.0"
  bind_port = 8000
  riva_address = "localhost:50051"          # 段階6-2-b 以降で使用
  llm_url = "http://localhost:8080/v1/chat/completions"  # 段階6-2-c 以降で使用

段階6-2-a で実用するのは bind_host / bind_port / default_sample_rate /
default_language / ping_interval / ping_timeout のみ。riva_address / llm_url /
glossary / system_prompt は後段で利用するための定義を先取りしている。
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Config:
    # WebSocket bind
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    path: str = "/v1/dictation"

    # クライアントが start で audio を指定しなかった場合のデフォルト
    default_sample_rate: int = 16000
    default_language: str = "ja-JP"

    # WebSocket レイヤのキープアライブ (秒)。仕様 §8 に合わせて 30。
    ping_interval: int = 30
    ping_timeout: int = 20

    # 段階6-2-b 以降で使用 (本ファイルでは保持のみ)
    riva_address: str = "localhost:50051"

    # 段階6-2-c 以降で使用 (本ファイルでは保持のみ)
    llm_url: str = "http://localhost:8080/v1/chat/completions"
    glossary: list[str] = field(default_factory=list)
    system_prompt: str = ""


_ENV_MAP: dict[str, tuple[str, type]] = {
    "KOECAST_BIND_HOST": ("bind_host", str),
    "KOECAST_BIND_PORT": ("bind_port", int),
    "KOECAST_PATH": ("path", str),
    "KOECAST_RIVA_ADDRESS": ("riva_address", str),
    "KOECAST_LLM_URL": ("llm_url", str),
}


def _load_file_overrides() -> dict[str, Any]:
    config_path_env = os.environ.get("KOECAST_CONFIG")
    if config_path_env:
        config_path = Path(config_path_env)
    else:
        config_path = Path.home() / ".config" / "koecast" / "config.toml"
    if not config_path.is_file():
        return {}
    with config_path.open("rb") as f:
        data = tomllib.load(f)
    valid_fields = {f.name for f in fields(Config)}
    return {k: v for k, v in data.items() if k in valid_fields}


def _load_env_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for env_key, (field_name, caster) in _ENV_MAP.items():
        if (raw := os.environ.get(env_key)) is not None:
            overrides[field_name] = caster(raw)
    return overrides


def load_config() -> Config:
    """ファイル → 環境変数 → デフォルトの順で解決した Config を返す。"""
    merged: dict[str, Any] = {**_load_file_overrides(), **_load_env_overrides()}
    return Config(**merged)
