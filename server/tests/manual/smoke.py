"""段階6-2-a の動作確認用 smoke クライアント (wscat 代替)。

pytest 自動収集対象外 (tests/manual/ 配下)。サーバ起動状態で別端末から手動実行する想定。

実行:
    cd server
    uv run python -m tests.manual.smoke

サーバ側:
    cd server
    uv run python -m dictation_gateway
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import websockets

DEFAULT_URL = os.environ.get(
    "KOECAST_SMOKE_URL", "ws://127.0.0.1:8000/v1/dictation"
)


async def _recv_json(ws) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
    if isinstance(raw, bytes):
        raise AssertionError(f"expected Text frame, got Binary: {raw!r}")
    return json.loads(raw)


async def scenario_happy_path(url: str) -> None:
    print(f"\n=== scenario_happy_path ({url}) ===")
    async with websockets.connect(url) as ws:
        ready = await _recv_json(ws)
        print(f"  recv ready: {ready}")
        assert ready["type"] == "ready", ready
        assert ready["protocol_version"] == 1, ready
        assert ready["defaults"]["language"] == "ja-JP", ready

        # start (minimal)
        await ws.send(json.dumps({"type": "start", "protocol_version": 1}))
        print("  sent start (minimal)")

        # PCM binary を 3 chunk 投げる (LISTENING 中なので捨てられる)
        for i in range(3):
            await ws.send(b"\x00\x01\x02\x03" * 400)  # 1600 bytes
        print("  sent 3 binary chunks (LISTENING, discarded)")

        # stop
        await ws.send(json.dumps({"type": "stop"}))
        print("  sent stop")

        session_end = await _recv_json(ws)
        print(f"  recv session_end: {session_end}")
        assert session_end == {"type": "session_end", "segments": 0}, session_end

    print("  OK: ready → start → binary → stop → session_end")


async def scenario_unknown_type(url: str) -> None:
    print(f"\n=== scenario_unknown_type ({url}) ===")
    async with websockets.connect(url) as ws:
        ready = await _recv_json(ws)
        assert ready["type"] == "ready"

        await ws.send(json.dumps({"type": "telepathy"}))
        print("  sent {type: telepathy}")
        err = await _recv_json(ws)
        print(f"  recv error: {err}")
        assert err["type"] == "error"
        assert err["code"] == "BAD_MESSAGE"
        assert err["recoverable"] is True

        # 接続は維持されているはず、再度正しいメッセージで session を回せる
        await ws.send(json.dumps({"type": "start", "protocol_version": 1}))
        await ws.send(json.dumps({"type": "stop"}))
        session_end = await _recv_json(ws)
        assert session_end["type"] == "session_end"

    print("  OK: BAD_MESSAGE は recoverable=true で接続維持、後続セッションが回る")


async def scenario_unsupported_version(url: str) -> None:
    print(f"\n=== scenario_unsupported_version ({url}) ===")
    async with websockets.connect(url) as ws:
        ready = await _recv_json(ws)
        assert ready["type"] == "ready"

        await ws.send(json.dumps({"type": "start", "protocol_version": 2}))
        print("  sent {type: start, protocol_version: 2}")
        err = await _recv_json(ws)
        print(f"  recv error: {err}")
        assert err["type"] == "error"
        assert err["code"] == "UNSUPPORTED_VERSION"
        assert err["recoverable"] is False

        # サーバが接続を閉じることを確認
        try:
            extra = await asyncio.wait_for(ws.recv(), timeout=2.0)
            raise AssertionError(f"expected closed connection, got: {extra!r}")
        except websockets.exceptions.ConnectionClosed:
            pass
    print("  OK: UNSUPPORTED_VERSION は recoverable=false で接続クローズ")


async def scenario_state_violation(url: str) -> None:
    print(f"\n=== scenario_state_violation ({url}) ===")
    async with websockets.connect(url) as ws:
        ready = await _recv_json(ws)
        assert ready["type"] == "ready"

        # READY で stop を送る → BAD_MESSAGE
        await ws.send(json.dumps({"type": "stop"}))
        print("  sent stop (in READY state)")
        err = await _recv_json(ws)
        print(f"  recv error: {err}")
        assert err["type"] == "error"
        assert err["code"] == "BAD_MESSAGE"
        assert err["recoverable"] is True

        # LISTENING で start を送る → BAD_MESSAGE
        await ws.send(json.dumps({"type": "start", "protocol_version": 1}))
        await ws.send(json.dumps({"type": "start", "protocol_version": 1}))
        print("  sent start, then start again (in LISTENING state)")
        err2 = await _recv_json(ws)
        print(f"  recv error: {err2}")
        assert err2["type"] == "error"
        assert err2["code"] == "BAD_MESSAGE"
        assert err2["recoverable"] is True

        # stop で正常に閉じる
        await ws.send(json.dumps({"type": "stop"}))
        session_end = await _recv_json(ws)
        assert session_end["type"] == "session_end"

    print("  OK: 状態違反は BAD_MESSAGE recoverable=true で接続維持")


async def scenario_ping(url: str) -> None:
    print(f"\n=== scenario_ping ({url}) ===")
    async with websockets.connect(url) as ws:
        ready = await _recv_json(ws)
        assert ready["type"] == "ready"

        # アプリレイヤ ping は応答なし (仕様 §4.4 / §8)
        await ws.send(json.dumps({"type": "ping"}))
        print("  sent {type: ping}")
        try:
            extra = await asyncio.wait_for(ws.recv(), timeout=1.5)
            raise AssertionError(f"expected no response, got: {extra!r}")
        except asyncio.TimeoutError:
            pass

        # 続けて start/stop で session を回せる
        await ws.send(json.dumps({"type": "start", "protocol_version": 1}))
        await ws.send(json.dumps({"type": "stop"}))
        session_end = await _recv_json(ws)
        assert session_end["type"] == "session_end"

    print("  OK: app-level ping は応答なし、接続は維持")


async def scenario_invalid_json(url: str) -> None:
    print(f"\n=== scenario_invalid_json ({url}) ===")
    async with websockets.connect(url) as ws:
        ready = await _recv_json(ws)
        assert ready["type"] == "ready"

        await ws.send("this is not json")
        print("  sent 'this is not json'")
        err = await _recv_json(ws)
        print(f"  recv error: {err}")
        assert err["type"] == "error"
        assert err["code"] == "BAD_MESSAGE"
        assert err["recoverable"] is True
    print("  OK: 不正 JSON は BAD_MESSAGE recoverable=true")


async def scenario_wrong_path(url: str) -> None:
    print(f"\n=== scenario_wrong_path ===")
    wrong = url.rsplit("/", 1)[0] + "/wrong"
    print(f"  trying {wrong}")
    try:
        async with websockets.connect(wrong):
            raise AssertionError("expected handshake to fail with 404")
    except websockets.exceptions.InvalidStatus as e:
        print(f"  got expected handshake error: {e}")
    print("  OK: 404 not found")


async def main() -> int:
    url = DEFAULT_URL
    print(f"target: {url}")
    try:
        await scenario_happy_path(url)
        await scenario_unknown_type(url)
        await scenario_unsupported_version(url)
        await scenario_state_violation(url)
        await scenario_ping(url)
        await scenario_invalid_json(url)
        await scenario_wrong_path(url)
    except Exception as e:
        print(f"\nFAILED: {type(e).__name__}: {e}")
        return 1
    print("\nALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
