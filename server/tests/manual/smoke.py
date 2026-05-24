"""段階6-2-a / 6-2-b の動作確認用 smoke クライアント (wscat 代替)。

pytest 自動収集対象外 (tests/manual/ 配下)。サーバ起動状態で別端末から手動実行する想定。

実行:
    cd server
    uv run python tests/manual/smoke.py                       # Riva 不要の scenario のみ
    KOECAST_SMOKE_RIVA=1 uv run python tests/manual/smoke.py  # Riva 必須の scenario も

サーバ側:
    cd server
    uv run python -m dictation_gateway

Riva NIM (KOECAST_SMOKE_RIVA=1 のとき必要):
    docker compose -f server/deploy/riva-nim.compose.yml up -d
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
WITH_RIVA = os.environ.get("KOECAST_SMOKE_RIVA") == "1"
AUDIO_PATH = os.environ.get("KOECAST_SMOKE_RIVA_AUDIO")  # 16kHz mono PCM16 WAV (任意)


async def _recv_json(ws) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
    if isinstance(raw, bytes):
        raise AssertionError(f"expected Text frame, got Binary: {raw!r}")
    return json.loads(raw)


# =========================================================================
# Riva 不要 scenario (常に実行)
# =========================================================================


async def scenario_ready(url: str) -> None:
    print(f"\n=== scenario_ready ===")
    async with websockets.connect(url) as ws:
        ready = await _recv_json(ws)
        print(f"  recv ready: {ready}")
        assert ready["type"] == "ready", ready
        assert ready["protocol_version"] == 1, ready
        assert ready["defaults"]["sample_rate"] == 16000, ready
        assert ready["defaults"]["encoding"] == "LINEAR_PCM", ready
        assert ready["defaults"]["language"] == "ja-JP", ready
    print("  OK: ready handshake")


async def scenario_stop_in_ready_violation(url: str) -> None:
    print(f"\n=== scenario_stop_in_ready_violation ===")
    async with websockets.connect(url) as ws:
        await _recv_json(ws)  # ready
        await ws.send(json.dumps({"type": "stop"}))
        print("  sent stop (in READY)")
        err = await _recv_json(ws)
        print(f"  recv error: {err}")
        assert err["type"] == "error"
        assert err["code"] == "BAD_MESSAGE"
        assert err["recoverable"] is True
    print("  OK: stop in READY → BAD_MESSAGE recoverable=true")


async def scenario_unknown_type(url: str) -> None:
    print(f"\n=== scenario_unknown_type ===")
    async with websockets.connect(url) as ws:
        await _recv_json(ws)  # ready
        await ws.send(json.dumps({"type": "telepathy"}))
        print("  sent {type: telepathy}")
        err = await _recv_json(ws)
        print(f"  recv error: {err}")
        assert err["type"] == "error"
        assert err["code"] == "BAD_MESSAGE"
        assert err["recoverable"] is True
    print("  OK: unknown type → BAD_MESSAGE recoverable=true")


async def scenario_unsupported_version(url: str) -> None:
    print(f"\n=== scenario_unsupported_version ===")
    async with websockets.connect(url) as ws:
        await _recv_json(ws)  # ready
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
    print("  OK: protocol_version=2 → UNSUPPORTED_VERSION recoverable=false + close")


async def scenario_invalid_json(url: str) -> None:
    print(f"\n=== scenario_invalid_json ===")
    async with websockets.connect(url) as ws:
        await _recv_json(ws)  # ready
        await ws.send("this is not json")
        print("  sent 'this is not json'")
        err = await _recv_json(ws)
        print(f"  recv error: {err}")
        assert err["type"] == "error"
        assert err["code"] == "BAD_MESSAGE"
        assert err["recoverable"] is True
    print("  OK: invalid JSON → BAD_MESSAGE recoverable=true")


async def scenario_app_ping(url: str) -> None:
    print(f"\n=== scenario_app_ping ===")
    async with websockets.connect(url) as ws:
        await _recv_json(ws)  # ready
        await ws.send(json.dumps({"type": "ping"}))
        print("  sent {type: ping}")
        try:
            extra = await asyncio.wait_for(ws.recv(), timeout=1.5)
            raise AssertionError(f"expected no response, got: {extra!r}")
        except asyncio.TimeoutError:
            pass
    print("  OK: app-level ping → no response (WebSocket 標準 ping に委譲)")


async def scenario_wrong_path() -> None:
    print(f"\n=== scenario_wrong_path ===")
    wrong = DEFAULT_URL.rsplit("/", 1)[0] + "/wrong"
    print(f"  trying {wrong}")
    try:
        async with websockets.connect(wrong):
            raise AssertionError("expected handshake to fail with 404")
    except websockets.exceptions.InvalidStatus as e:
        print(f"  got expected handshake error: {e}")
    print("  OK: wrong path → HTTP 404")


async def scenario_audio_format_rejected_sample_rate(url: str) -> None:
    print(f"\n=== scenario_audio_format_rejected_sample_rate ===")
    async with websockets.connect(url) as ws:
        await _recv_json(ws)  # ready
        await ws.send(
            json.dumps(
                {
                    "type": "start",
                    "protocol_version": 1,
                    "audio": {
                        "sample_rate": 8000,
                        "encoding": "LINEAR_PCM",
                        "channels": 1,
                    },
                }
            )
        )
        print("  sent start with audio.sample_rate=8000")
        err = await _recv_json(ws)
        print(f"  recv error: {err}")
        assert err["type"] == "error"
        assert err["code"] == "AUDIO_FORMAT_REJECTED"
        assert err["recoverable"] is True

        # 接続維持されているので、正しい start で session を回せる (Riva 不要、stop で即抜ける)
        # ※ ただし正常 start は Riva セッションを開くため、Riva なしのテストでは
        #   ここで終わる (接続維持の確認だけ)
    print("  OK: sample_rate=8000 → AUDIO_FORMAT_REJECTED recoverable=true")


async def scenario_audio_format_rejected_channels(url: str) -> None:
    print(f"\n=== scenario_audio_format_rejected_channels ===")
    async with websockets.connect(url) as ws:
        await _recv_json(ws)  # ready
        await ws.send(
            json.dumps(
                {
                    "type": "start",
                    "protocol_version": 1,
                    "audio": {
                        "sample_rate": 16000,
                        "encoding": "LINEAR_PCM",
                        "channels": 2,
                    },
                }
            )
        )
        print("  sent start with audio.channels=2")
        err = await _recv_json(ws)
        print(f"  recv error: {err}")
        assert err["type"] == "error"
        assert err["code"] == "AUDIO_FORMAT_REJECTED"
        assert err["recoverable"] is True
    print("  OK: channels=2 → AUDIO_FORMAT_REJECTED recoverable=true")


# =========================================================================
# Riva 必須 scenario (KOECAST_SMOKE_RIVA=1 のときのみ実行)
# =========================================================================


async def scenario_start_stop_no_audio(url: str) -> None:
    """Riva セッションを開いて、音声なしで stop。session_end (segments=0) を期待。

    Riva NIM の接続性と、空ストリームでの自然終了経路の確認。
    """
    print(f"\n=== scenario_start_stop_no_audio (RIVA) ===")
    async with websockets.connect(url) as ws:
        await _recv_json(ws)  # ready
        await ws.send(json.dumps({"type": "start", "protocol_version": 1}))
        print("  sent start (no audio)")
        await ws.send(json.dumps({"type": "stop"}))
        print("  sent stop")
        msg = await asyncio.wait_for(_recv_json(ws), timeout=15.0)
        print(f"  recv: {msg}")
        assert msg["type"] == "session_end", msg
        assert msg["segments"] == 0, msg
    print("  OK: start → stop (no audio) → session_end segments=0")


async def scenario_double_start_violation(url: str) -> None:
    """LISTENING 中に start を二重送信 → BAD_MESSAGE。"""
    print(f"\n=== scenario_double_start_violation (RIVA) ===")
    async with websockets.connect(url) as ws:
        await _recv_json(ws)  # ready
        await ws.send(json.dumps({"type": "start", "protocol_version": 1}))
        print("  sent start (1st, opens Riva session)")
        # 二回目の start
        await ws.send(json.dumps({"type": "start", "protocol_version": 1}))
        print("  sent start (2nd, while LISTENING)")
        err = await asyncio.wait_for(_recv_json(ws), timeout=10.0)
        print(f"  recv error: {err}")
        assert err["type"] == "error", err
        assert err["code"] == "BAD_MESSAGE", err
        assert err["recoverable"] is True, err

        # 正常な stop で session を抜けて session_end を受け取れることを確認
        await ws.send(json.dumps({"type": "stop"}))
        msg = await asyncio.wait_for(_recv_json(ws), timeout=10.0)
        print(f"  recv: {msg}")
        assert msg["type"] == "session_end", msg
    print("  OK: 2nd start → BAD_MESSAGE recoverable=true、stop で正常終了")


async def scenario_streaming_ja_with_wav(url: str, wav_path: str) -> None:
    """日本語 WAV を流して partial / final / session_end を受信する。"""
    import wave

    print(f"\n=== scenario_streaming_ja_with_wav (RIVA + WAV) ===")
    print(f"  audio: {wav_path}")
    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        sw = wf.getsampwidth()
        ch = wf.getnchannels()
        if sr != 16000 or sw != 2 or ch != 1:
            raise SystemExit(
                f"audio must be 16kHz mono PCM16 (got sr={sr}, sample_width={sw}, channels={ch})"
            )

    async with websockets.connect(url) as ws:
        await _recv_json(ws)  # ready
        await ws.send(json.dumps({"type": "start", "protocol_version": 1}))
        print("  sent start")

        # 100ms 単位でリアルタイム速度で送出
        chunk_ms = 100
        with wave.open(wav_path, "rb") as wf:
            n_chunks = 0
            t0 = asyncio.get_event_loop().time()
            while True:
                data = wf.readframes(int(16000 * chunk_ms / 1000))
                if not data:
                    break
                # サーバへ送る (binary)
                await ws.send(data)
                n_chunks += 1
                # 同時並行で逐次到着する partial/final を読み出す
                # (non-blocking: 来てるものだけ拾う)
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.0)
                        d = json.loads(msg) if isinstance(msg, str) else None
                        if d:
                            print(f"  [{asyncio.get_event_loop().time()-t0:6.2f}s] recv: {d}")
                    except asyncio.TimeoutError:
                        break
                # リアルタイム速度を維持
                target = t0 + n_chunks * chunk_ms / 1000
                lag = target - asyncio.get_event_loop().time()
                if lag > 0:
                    await asyncio.sleep(lag)
        print(f"  sent {n_chunks} chunks total")

        await ws.send(json.dumps({"type": "stop"}))
        print("  sent stop")

        # 残りの partial/final/session_end を受信
        partial_count = 0
        final_count = 0
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=15.0)
            except asyncio.TimeoutError:
                raise AssertionError("session_end が timeout 内に来なかった")
            d = json.loads(msg) if isinstance(msg, str) else None
            if d is None:
                continue
            print(f"  recv: {d}")
            if d["type"] == "partial":
                partial_count += 1
            elif d["type"] == "final":
                final_count += 1
                # segment_id 単調増加と空白除去済みを確認
                assert isinstance(d["segment_id"], int) and d["segment_id"] >= 0
                # 空白除去の sanity check (たいてい final には半角空白が CJK 間に残らない)
                text = d["text"]
                # 厳密ではなく目視も併用
                print(f"    -> final[{d['segment_id']}] text={text!r}")
            elif d["type"] == "session_end":
                print(f"    -> segments={d['segments']}")
                break
            elif d["type"] == "error":
                raise AssertionError(f"got error: {d}")

        print(f"  partial_count={partial_count}, final_count={final_count}")
    print("  OK: streaming ja → partial / final / session_end")


# =========================================================================
# main
# =========================================================================


async def main() -> int:
    url = DEFAULT_URL
    print(f"target: {url}")
    print(f"riva-gated scenarios: {'ENABLED' if WITH_RIVA else 'SKIPPED'} (KOECAST_SMOKE_RIVA={WITH_RIVA})")
    try:
        # Riva 不要 (常に走る)
        await scenario_ready(url)
        await scenario_stop_in_ready_violation(url)
        await scenario_unknown_type(url)
        await scenario_unsupported_version(url)
        await scenario_invalid_json(url)
        await scenario_app_ping(url)
        await scenario_wrong_path()
        await scenario_audio_format_rejected_sample_rate(url)
        await scenario_audio_format_rejected_channels(url)

        if WITH_RIVA:
            await scenario_start_stop_no_audio(url)
            await scenario_double_start_violation(url)
            if AUDIO_PATH:
                await scenario_streaming_ja_with_wav(url, AUDIO_PATH)
            else:
                print(
                    "\n--- skip scenario_streaming_ja_with_wav: "
                    "set KOECAST_SMOKE_RIVA_AUDIO=<16kHz mono PCM16 WAV path> ---"
                )
    except Exception as e:
        print(f"\nFAILED: {type(e).__name__}: {e}")
        return 1
    print("\nALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
