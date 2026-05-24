"""protocol/examples/ のゴールデン例に対する契約テスト。

各 example について以下の2点を検証する:
  1. JSON Schema (Draft 2020-12) に対して valid である
  2. Pydantic モデルでパース → model_dump(mode="json", exclude_unset=True) で再
     シリアライズした結果が、元の JSON と dict として等価である

設計ドキュメント monorepo-design.md セクション 3.4 のゴールデンメッセージ契約テスト。
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import TypeAdapter

from dictation_gateway.protocol import ClientMessage, ServerMessage

PROTOCOL_ROOT = Path(__file__).resolve().parents[2] / "protocol"
SCHEMA_DIR = PROTOCOL_ROOT / "schema"
EXAMPLES_DIR = PROTOCOL_ROOT / "examples"

_C2S_SCHEMA = json.loads((SCHEMA_DIR / "client-to-server.schema.json").read_text())
_S2C_SCHEMA = json.loads((SCHEMA_DIR / "server-to-client.schema.json").read_text())

C2S_VALIDATOR = jsonschema.Draft202012Validator(_C2S_SCHEMA)
S2C_VALIDATOR = jsonschema.Draft202012Validator(_S2C_SCHEMA)

ClientAdapter: TypeAdapter = TypeAdapter(ClientMessage)
ServerAdapter: TypeAdapter = TypeAdapter(ServerMessage)

# (example file name, jsonschema validator, pydantic adapter)
CASES: list[tuple[str, jsonschema.Draft202012Validator, TypeAdapter]] = [
    ("start.json",       C2S_VALIDATOR, ClientAdapter),
    ("start_min.json",   C2S_VALIDATOR, ClientAdapter),
    ("stop.json",        C2S_VALIDATOR, ClientAdapter),
    ("ping.json",        C2S_VALIDATOR, ClientAdapter),
    ("ready.json",       S2C_VALIDATOR, ServerAdapter),
    ("partial.json",     S2C_VALIDATOR, ServerAdapter),
    ("final.json",       S2C_VALIDATOR, ServerAdapter),
    ("formatted.json",   S2C_VALIDATOR, ServerAdapter),
    ("session_end.json", S2C_VALIDATOR, ServerAdapter),
    ("error.json",       S2C_VALIDATOR, ServerAdapter),
]


@pytest.mark.parametrize(
    "name,validator,adapter", CASES, ids=[c[0] for c in CASES]
)
def test_example_is_valid_against_jsonschema(name, validator, adapter):
    original = json.loads((EXAMPLES_DIR / name).read_text())
    errors = list(validator.iter_errors(original))
    assert not errors, f"{name} は JSON Schema で valid でない: {errors}"


@pytest.mark.parametrize(
    "name,validator,adapter", CASES, ids=[c[0] for c in CASES]
)
def test_example_roundtrips_via_pydantic(name, validator, adapter):
    original = json.loads((EXAMPLES_DIR / name).read_text())
    parsed = adapter.validate_python(original)
    redumped = adapter.dump_python(parsed, mode="json", exclude_unset=True)
    assert original == redumped, (
        f"drift in {name}\n"
        f"  original: {json.dumps(original, ensure_ascii=False, sort_keys=True)}\n"
        f"  redumped: {json.dumps(redumped, ensure_ascii=False, sort_keys=True)}"
    )
