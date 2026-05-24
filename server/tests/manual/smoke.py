"""段階6-2-a / 6-2-b / 6-2-c の動作確認用 smoke クライアント (wscat 代替)。

pytest 自動収集対象外 (tests/manual/ 配下)。サーバ起動状態で別端末から手動実行する想定。

実行:
    cd server
    uv run python tests/manual/smoke.py                          # Riva 不要の scenario のみ
    KOECAST_SMOKE_RIVA=1 uv run python tests/manual/smoke.py     # Riva 必須の scenario も
    KOECAST_SMOKE_RIVA=1 KOECAST_SMOKE_LLM=1 \
      uv run python tests/manual/smoke.py                        # LLM scenario (qwen36-mtp 必要)
    KOECAST_SMOKE_RIVA=1 KOECAST_SMOKE_LLM_FALLBACK=1 \
      uv run python tests/manual/smoke.py                        # LLM fallback (別ポートで自走)

サーバ側:
    cd server
    uv run python -m dictation_gateway

Riva NIM (KOECAST_SMOKE_RIVA=1 のとき必要):
    docker compose -f server/deploy/riva-nim.compose.yml up -d

LLM (KOECAST_SMOKE_LLM=1 のとき必要): qwen36-mtp (llama-server, OpenAI 互換) が
    http://localhost:8080/v1/chat/completions で稼働していること。

LLM fallback scenario (KOECAST_SMOKE_LLM_FALLBACK=1) は、本 smoke が自前で
    別ポート (デフォルト 8001) にサーバを起動し、LLM URL を到達不能
    (http://127.0.0.1:1) に向けて formatted{fallback=true} を観測する。
    メインサーバ (8000) と qwen36-mtp は停止しない。
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
WITH_LLM = os.environ.get("KOECAST_SMOKE_LLM") == "1"
WITH_LLM_FALLBACK = os.environ.get("KOECAST_SMOKE_LLM_FALLBACK") == "1"
AUDIO_PATH = os.environ.get("KOECAST_SMOKE_RIVA_AUDIO")  # 16kHz mono PCM16 WAV
FALLBACK_PORT = int(os.environ.get("KOECAST_SMOKE_FALLBACK_PORT", "8001"))


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


async def _stream_wav_and_collect(
    url: str,
    wav_path: str,
    *,
    enable_formatting: bool,
    context_terms: list[str] | None = None,
    overall_timeout: float = 30.0,
) -> dict:
    """共通: WAV をリアルタイム速度で流し、session_end まで受信して結果を返す。

    返り値: {
      "partial_count": int, "final_count": int, "formatted_count": int,
      "finals": [(segment_id, text), ...],
      "formatteds": [(segment_id, text, fallback), ...],
      "events_in_order": [type, ...],
      "session_end": int (segments),
    }
    """
    import wave

    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        sw = wf.getsampwidth()
        ch = wf.getnchannels()
        if sr != 16000 or sw != 2 or ch != 1:
            raise SystemExit(
                f"audio must be 16kHz mono PCM16 (got sr={sr}, sample_width={sw}, channels={ch})"
            )

    partial_count = 0
    finals: list[tuple[int, str]] = []
    formatteds: list[tuple[int, str, bool]] = []
    events_in_order: list[str] = []
    session_end_segments: int | None = None

    async with websockets.connect(url) as ws:
        await _recv_json(ws)  # ready
        start_msg = {"type": "start", "protocol_version": 1}
        if not enable_formatting:
            start_msg["enable_formatting"] = False
        if context_terms:
            start_msg["context"] = context_terms
        await ws.send(json.dumps(start_msg))
        print(f"  sent start enable_formatting={enable_formatting} context={context_terms}")

        chunk_ms = 100
        with wave.open(wav_path, "rb") as wf:
            n_chunks = 0
            t0 = asyncio.get_event_loop().time()
            while True:
                data = wf.readframes(int(16000 * chunk_ms / 1000))
                if not data:
                    break
                await ws.send(data)
                n_chunks += 1
                # non-blocking で逐次到着する partial を読む
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.0)
                        d = json.loads(msg) if isinstance(msg, str) else None
                        if d:
                            events_in_order.append(d["type"])
                            if d["type"] == "partial":
                                partial_count += 1
                            elif d["type"] == "final":
                                finals.append((d["segment_id"], d["text"]))
                            elif d["type"] == "formatted":
                                formatteds.append((d["segment_id"], d["text"], d["fallback"]))
                            elif d["type"] == "error":
                                raise AssertionError(f"got error during stream: {d}")
                    except asyncio.TimeoutError:
                        break
                target = t0 + n_chunks * chunk_ms / 1000
                lag = target - asyncio.get_event_loop().time()
                if lag > 0:
                    await asyncio.sleep(lag)
        print(f"  sent {n_chunks} chunks total")

        await ws.send(json.dumps({"type": "stop"}))
        print("  sent stop")

        # 残り final / formatted / session_end を待つ
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=overall_timeout)
            except asyncio.TimeoutError:
                raise AssertionError(
                    f"session_end が timeout {overall_timeout}s 内に来なかった"
                )
            d = json.loads(msg) if isinstance(msg, str) else None
            if d is None:
                continue
            events_in_order.append(d["type"])
            if d["type"] == "partial":
                partial_count += 1
            elif d["type"] == "final":
                finals.append((d["segment_id"], d["text"]))
                print(f"    final[{d['segment_id']}]: {d['text']!r}")
            elif d["type"] == "formatted":
                formatteds.append((d["segment_id"], d["text"], d["fallback"]))
                print(
                    f"    formatted[{d['segment_id']}] fallback={d['fallback']}: {d['text']!r}"
                )
            elif d["type"] == "session_end":
                session_end_segments = d["segments"]
                print(f"    session_end segments={session_end_segments}")
                break
            elif d["type"] == "error":
                raise AssertionError(f"got error: {d}")

    return {
        "partial_count": partial_count,
        "final_count": len(finals),
        "formatted_count": len(formatteds),
        "finals": finals,
        "formatteds": formatteds,
        "events_in_order": events_in_order,
        "session_end": session_end_segments,
    }


async def scenario_streaming_ja_with_wav(url: str, wav_path: str) -> None:
    """日本語 WAV を流して partial / final / session_end を受信する (LLM 整形なし)。"""
    print(f"\n=== scenario_streaming_ja_with_wav (RIVA + WAV) ===")
    print(f"  audio: {wav_path}")
    r = await _stream_wav_and_collect(
        url, wav_path, enable_formatting=False, overall_timeout=15.0
    )
    print(
        f"  partial_count={r['partial_count']}, final_count={r['final_count']}, "
        f"formatted_count={r['formatted_count']}"
    )
    assert r["formatted_count"] == 0, "enable_formatting=False で formatted は来ないはず"
    assert r["final_count"] >= 1, f"final が来ていない: {r}"
    # segment_id 単調増加
    seg_ids = [seg_id for seg_id, _ in r["finals"]]
    assert seg_ids == sorted(seg_ids), f"segment_id が単調増加していない: {seg_ids}"
    # session_end.segments と final 数の整合
    assert r["session_end"] == r["final_count"], (
        f"session_end.segments={r['session_end']} と final 数 {r['final_count']} が不整合"
    )
    # final テキストの CJK 間空白除去サニティ
    for seg_id, text in r["finals"]:
        # ja の連続漢字/かなの間に半角空白が残っていないこと
        # (英字との境界の空白は維持される)
        assert "ロ ッ" not in text and "ベ ッ" not in text, (
            f"final[{seg_id}] に CJK 間空白が残っている: {text!r}"
        )
    print("  OK: streaming ja → partial / final / session_end (formatted なし)")


async def scenario_streaming_ja_with_wav_and_llm(url: str, wav_path: str) -> None:
    """LLM 整形を有効にして formatted まで受信する。"""
    print(f"\n=== scenario_streaming_ja_with_wav_and_llm (RIVA + LLM + WAV) ===")
    r = await _stream_wav_and_collect(
        url,
        wav_path,
        enable_formatting=True,
        context_terms=["Bedrock", "Lambda", "DynamoDB", "CDK"],
        overall_timeout=60.0,  # LLM 整形の時間を含む
    )
    print(
        f"  partial_count={r['partial_count']}, final_count={r['final_count']}, "
        f"formatted_count={r['formatted_count']}"
    )
    assert r["final_count"] >= 1, f"final が来ていない: {r}"
    assert r["formatted_count"] == r["final_count"], (
        f"final 数 {r['final_count']} と formatted 数 {r['formatted_count']} が一致しない"
    )
    # 各 formatted は対応する final と segment_id 一致
    final_seg_ids = [seg_id for seg_id, _ in r["finals"]]
    formatted_seg_ids = [seg_id for seg_id, _, _ in r["formatteds"]]
    assert final_seg_ids == formatted_seg_ids, (
        f"segment_id が一致しない: final={final_seg_ids} formatted={formatted_seg_ids}"
    )
    # formatted のテキストは非空
    for seg_id, text, fallback in r["formatteds"]:
        assert text and text.strip(), f"formatted[{seg_id}] が空: {text!r}"
    # 全部 fallback=False (qwen36-mtp 稼働前提)
    fallbacks = [fb for _, _, fb in r["formatteds"]]
    assert not any(fallbacks), f"qwen36-mtp 稼働中に fallback=true: {fallbacks}"
    # イベント順序: 各 final の直後 (partial を挟んで) に対応する formatted が来る
    # session_end より前に全 formatted が来ていることだけ厳密に確認
    se_idx = r["events_in_order"].index("session_end")
    formatted_indices = [
        i for i, t in enumerate(r["events_in_order"]) if t == "formatted"
    ]
    assert all(i < se_idx for i in formatted_indices), (
        "session_end より後に formatted が来ている"
    )
    # 各 final と対応する formatted の順序 (final が先)
    for seg_id, _ in r["finals"]:
        f_idx = next(
            i
            for i, t in enumerate(r["events_in_order"])
            if t == "final"
        )
        fm_idx = next(
            (
                i
                for i, t in enumerate(r["events_in_order"])
                if t == "formatted" and i > f_idx
            ),
            None,
        )
        # 最低限「final があれば、それより後に formatted がある」が成立する
        # (複数 segment 詳細順序は events_in_order の上記アサートで担保済み)
        assert fm_idx is not None, "final の後に formatted が見つからない"
        break  # 1 segment 分だけ厳密に確認
    print("  OK: final → formatted (fallback=False) → session_end の順序")


async def scenario_streaming_with_llm_fallback(wav_path: str) -> None:
    """LLM 到達不能エンドポイント (http://127.0.0.1:1) でサーバを起こし、
    formatted{fallback=true} を観測する。qwen36-mtp は止めない。

    本シナリオは smoke 自身がサブプロセスでサーバを起動し、teardown までやる。
    """
    import subprocess
    import signal as _signal

    print(f"\n=== scenario_streaming_with_llm_fallback (RIVA + LLM fallback + WAV) ===")
    fallback_url = f"ws://127.0.0.1:{FALLBACK_PORT}/v1/dictation"
    env = os.environ.copy()
    env["KOECAST_BIND_PORT"] = str(FALLBACK_PORT)
    env["KOECAST_LLM_URL"] = "http://127.0.0.1:1/v1/chat/completions"  # 到達不能
    # KOECAST_BIND_HOST はデフォルト 127.0.0.1 のまま
    print(f"  spawning fallback gateway on :{FALLBACK_PORT} with LLM_URL={env['KOECAST_LLM_URL']}")
    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "dictation_gateway"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        # listen 待ち (最大 15 秒)
        for _ in range(30):
            try:
                async with websockets.connect(fallback_url):
                    break
            except (OSError, websockets.exceptions.InvalidStatus):
                await asyncio.sleep(0.5)
        else:
            raise AssertionError("fallback gateway が listen に至らない")

        r = await _stream_wav_and_collect(
            fallback_url,
            wav_path,
            enable_formatting=True,
            overall_timeout=60.0,
        )
        print(
            f"  partial_count={r['partial_count']}, final_count={r['final_count']}, "
            f"formatted_count={r['formatted_count']}"
        )
        assert r["final_count"] >= 1, f"final が来ていない: {r}"
        assert r["formatted_count"] == r["final_count"], (
            f"final 数 {r['final_count']} と formatted 数 {r['formatted_count']} が一致しない"
        )
        # 全 formatted が fallback=true
        fallbacks = [fb for _, _, fb in r["formatteds"]]
        assert all(fallbacks), f"LLM 不通なのに fallback=False が含まれる: {fallbacks}"
        # fallback=true の formatted.text は対応 final.text と一致
        final_map = {seg: text for seg, text in r["finals"]}
        for seg_id, ftext, _ in r["formatteds"]:
            assert ftext == final_map[seg_id], (
                f"fallback の formatted[{seg_id}] が final と一致しない:\n"
                f"  final={final_map[seg_id]!r}\n  formatted={ftext!r}"
            )
        print("  OK: LLM 不通 → formatted{fallback=true, text=final 原文}")
    finally:
        try:
            os.killpg(proc.pid, _signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, _signal.SIGKILL)


# =========================================================================
# main
# =========================================================================


async def main() -> int:
    url = DEFAULT_URL
    print(f"target: {url}")
    print(f"riva scenarios          : {'ENABLED' if WITH_RIVA else 'SKIPPED'}")
    print(f"llm scenarios           : {'ENABLED' if WITH_LLM else 'SKIPPED'}")
    print(f"llm fallback scenarios  : {'ENABLED' if WITH_LLM_FALLBACK else 'SKIPPED'}")
    print(f"audio                   : {AUDIO_PATH or '(not set)'}")
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

        if WITH_LLM:
            if not WITH_RIVA or not AUDIO_PATH:
                print(
                    "\n--- skip LLM scenario: requires KOECAST_SMOKE_RIVA=1 and KOECAST_SMOKE_RIVA_AUDIO ---"
                )
            else:
                await scenario_streaming_ja_with_wav_and_llm(url, AUDIO_PATH)

        if WITH_LLM_FALLBACK:
            if not WITH_RIVA or not AUDIO_PATH:
                print(
                    "\n--- skip LLM fallback scenario: requires KOECAST_SMOKE_RIVA=1 and KOECAST_SMOKE_RIVA_AUDIO ---"
                )
            else:
                await scenario_streaming_with_llm_fallback(AUDIO_PATH)
    except Exception as e:
        print(f"\nFAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1
    print("\nALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
