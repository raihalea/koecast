"""WebSocket サーバ本体と接続状態機械。

段階6-2-b スコープ:
  - 段階6-2-a の WebSocket スケルトン + 状態機械を維持
  - `start` で Riva ASR NIM セッションを開く
  - Binary フレーム (PCM) を riva_bridge 経由で Riva に転送
  - Riva の partial → `partial` メッセージ、is_final=True → `final` メッセージ
  - `stop` で Riva セッションを close → 残り final を flush → `session_end` を送る
  - エラー処理: `RIVA_UNAVAILABLE` (recoverable=false) / `RIVA_STREAM_ERROR` (recoverable=false)
  - `start.audio` の意味検証: sample_rate != 16000 / channels != 1 → `AUDIO_FORMAT_REJECTED`
  - `formatted` メッセージは段階6-2-c で追加 (本段階では送らない)

接続状態 (docs/dictation-gateway-protocol-v1.md §2):
  READY → (start) → LISTENING → (stop, session_end 送信) → READY → ...
"""
from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum
from typing import Any, Optional

import websockets
from pydantic import TypeAdapter, ValidationError
from websockets.asyncio.server import ServerConnection

from .config import Config, load_config
from .protocol import (
    ClientMessage,
    ErrorMessage,
    FinalMessage,
    PartialMessage,
    PingMessage,
    ReadyDefaults,
    ReadyMessage,
    ServerMessage,
    SessionEndMessage,
    StartMessage,
    StopMessage,
)
from .riva_bridge import (
    RivaBridge,
    RivaStreamError,
    RivaUnavailable,
    StreamingResult,
    _RivaSession,
)

logger = logging.getLogger(__name__)

ClientAdapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
ServerAdapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)

PROTOCOL_VERSION = 1
SERVER_ID = "dictation-gateway/0.1.0"

# Riva ASR NIM の制約。段階6-2-b で固定値とし、変更が必要なら config に格上げする。
ACCEPTED_SAMPLE_RATE = 16000
ACCEPTED_CHANNELS = 1


class State(str, Enum):
    READY = "READY"
    LISTENING = "LISTENING"


async def _send(ws: ServerConnection, msg: Any) -> None:
    data = ServerAdapter.dump_json(msg, exclude_unset=True).decode("utf-8")
    await ws.send(data)


async def _send_error(
    ws: ServerConnection,
    code: str,
    message: str,
    *,
    recoverable: bool,
    segment_id: Optional[int] = None,
) -> None:
    """error メッセージを送る。segment_id は省略可。"""
    kwargs: dict[str, Any] = {
        "type": "error",
        "code": code,
        "message": message,
        "recoverable": recoverable,
    }
    if segment_id is not None:
        kwargs["segment_id"] = segment_id
    await _send(ws, ErrorMessage(**kwargs))


def _is_unsupported_version_error(exc: ValidationError) -> bool:
    """ValidationError が `protocol_version` の Literal[1] 制約違反かを判定する。

    schema 上 `protocol_version` は const: 1 なので、2 を送ると Pydantic が
    "Input should be 1" 系のエラーを返す。
    """
    for err in exc.errors():
        loc = err.get("loc", ())
        if "protocol_version" in loc and err.get("type") in {
            "literal_error",
            "union_tag_invalid",
        }:
            return True
    return False


async def _forward_results(
    session: _RivaSession, ws: ServerConnection
) -> None:
    """Riva セッションの結果を WS に橋渡す。session 終了で自然終了する。"""
    try:
        async for result in session.results():
            assert isinstance(result, StreamingResult)
            if result.is_final:
                msg = FinalMessage(
                    type="final",
                    segment_id=result.segment_id,
                    text=result.text,
                )
            else:
                msg = PartialMessage(
                    type="partial",
                    segment_id=result.segment_id,
                    text=result.text,
                )
            await _send(ws, msg)
    except RivaUnavailable as e:
        # 接続全体に関するエラー: segment_id は付けない (仕様 §5.6)
        try:
            await _send_error(
                ws, "RIVA_UNAVAILABLE", str(e), recoverable=False
            )
            await ws.close()
        except websockets.exceptions.ConnectionClosed:
            pass
    except RivaStreamError as e:
        # セグメントに紐づくエラー: 進行中の segment_id を付ける
        try:
            await _send_error(
                ws,
                "RIVA_STREAM_ERROR",
                str(e),
                recoverable=False,
                segment_id=session.current_segment_id(),
            )
            await ws.close()
        except websockets.exceptions.ConnectionClosed:
            pass
    except websockets.exceptions.ConnectionClosed:
        # WS 側が先に閉じた場合は何もしない (session は finally で cleanup)
        pass
    except Exception:
        logger.exception("unexpected error in _forward_results")


async def handle_connection(ws: ServerConnection, config: Config) -> None:
    """1 WebSocket 接続を扱う。

    パス制約は serve() の process_request で済ませる前提なので、ここでは
    プロトコル制御だけに集中する。
    """
    peer = ws.remote_address
    logger.info("connection opened from %s", peer)

    ready = ReadyMessage(
        type="ready",
        protocol_version=PROTOCOL_VERSION,
        server=SERVER_ID,
        defaults=ReadyDefaults(
            sample_rate=config.default_sample_rate,
            encoding="LINEAR_PCM",
            language=config.default_language,
        ),
    )
    await _send(ws, ready)

    bridge = RivaBridge(config)
    state = State.READY
    current_session: Optional[_RivaSession] = None
    forward_task: Optional[asyncio.Task] = None

    async def end_current_session() -> int:
        """進行中の session を閉じて drain し、観測した final 数を返す (idempotent)。"""
        nonlocal current_session, forward_task
        if current_session is None:
            return 0
        await current_session.close()
        if forward_task is not None and not forward_task.done():
            try:
                await forward_task
            except Exception:
                logger.exception("forward_task raised during drain")
        finals = current_session.finals_seen
        bridge.commit_session(current_session)
        current_session = None
        forward_task = None
        return finals

    try:
        async for message in ws:
            # --- Binary フレーム (PCM) ---
            if isinstance(message, bytes):
                if state == State.LISTENING and current_session is not None:
                    await current_session.feed_audio(message)
                else:
                    # 仕様には明示なし。LISTENING 以外の Binary は race として無視。
                    logger.warning(
                        "binary frame received while state=%s, dropping", state.name
                    )
                continue

            # --- Text フレーム (JSON 制御メッセージ) ---
            try:
                parsed = ClientAdapter.validate_json(message)
            except ValidationError as e:
                if _is_unsupported_version_error(e):
                    await _send_error(
                        ws,
                        "UNSUPPORTED_VERSION",
                        f"protocol_version must be {PROTOCOL_VERSION}",
                        recoverable=False,
                    )
                    await ws.close()
                    break
                await _send_error(
                    ws, "BAD_MESSAGE", str(e), recoverable=True
                )
                continue
            except json.JSONDecodeError as e:
                await _send_error(
                    ws, "BAD_MESSAGE", f"invalid JSON: {e}", recoverable=True
                )
                continue

            if isinstance(parsed, StartMessage):
                if state != State.READY:
                    await _send_error(
                        ws,
                        "BAD_MESSAGE",
                        "`start` received while not in READY state",
                        recoverable=True,
                    )
                    continue

                # start.audio の意味検証 (Pydantic は構文検証のみ。AUDIO_FORMAT_REJECTED は
                # 仕様 §7 で recoverable=true なので state は READY のまま戻す)
                if parsed.audio is not None:
                    if parsed.audio.sample_rate != ACCEPTED_SAMPLE_RATE:
                        await _send_error(
                            ws,
                            "AUDIO_FORMAT_REJECTED",
                            f"unsupported sample_rate {parsed.audio.sample_rate}; "
                            f"must be {ACCEPTED_SAMPLE_RATE}",
                            recoverable=True,
                        )
                        continue
                    if parsed.audio.channels != ACCEPTED_CHANNELS:
                        await _send_error(
                            ws,
                            "AUDIO_FORMAT_REJECTED",
                            f"unsupported channels {parsed.audio.channels}; "
                            f"must be {ACCEPTED_CHANNELS}",
                            recoverable=True,
                        )
                        continue

                # 段階6-2-c: parsed.context を llm.py に橋渡す予定。
                # ここでは start を契機に Riva セッションを開く (eager)。
                current_session = bridge.open_session()
                forward_task = asyncio.create_task(
                    _forward_results(current_session, ws)
                )
                state = State.LISTENING
                continue

            if isinstance(parsed, StopMessage):
                if state != State.LISTENING:
                    await _send_error(
                        ws,
                        "BAD_MESSAGE",
                        "`stop` received while not in LISTENING state",
                        recoverable=True,
                    )
                    continue

                # session を閉じ、forward_task が drain するのを待ち、session_end を送る
                segments = await end_current_session()
                state = State.READY
                try:
                    await _send(
                        ws,
                        SessionEndMessage(type="session_end", segments=segments),
                    )
                except websockets.exceptions.ConnectionClosed:
                    break
                continue

            if isinstance(parsed, PingMessage):
                # 仕様 §4.4 / §8: WebSocket 標準 ping/pong で十分なので応答不要。
                continue

            # 未知の variant (現状到達不能、安全側で fallback)
            await _send_error(
                ws,
                "BAD_MESSAGE",
                f"unsupported message type: {type(parsed).__name__}",
                recoverable=True,
            )
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        # 切断時、進行中 session があれば cleanup する (二重 close は idempotent)
        try:
            await end_current_session()
        except Exception:
            logger.exception("error during session cleanup")
        logger.info("connection closed (peer=%s)", peer)


async def _process_request(connection, request):
    """パス制約 (/v1/dictation 固定) を WebSocket ハンドシェイク前に確認する。

    違うパスへの接続は 404 で返す。
    """
    expected = _expected_path
    if request.path != expected:
        return connection.respond(404, f"not found: {request.path}\n")
    return None


# serve() で起動時に固定する。process_request からの参照用。
_expected_path: str = "/v1/dictation"


async def serve(config: Config) -> None:
    """サーバを起動して停止シグナルまで動かす。"""
    global _expected_path
    _expected_path = config.path

    logger.info(
        "starting dictation-gateway on ws://%s:%d%s",
        config.bind_host,
        config.bind_port,
        config.path,
    )
    logger.info("riva endpoint: %s", config.riva_address)
    async with websockets.asyncio.server.serve(
        lambda ws: handle_connection(ws, config),
        config.bind_host,
        config.bind_port,
        process_request=_process_request,
        ping_interval=config.ping_interval,
        ping_timeout=config.ping_timeout,
    ):
        await asyncio.Future()  # 永続待ち


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()
    asyncio.run(serve(config))
