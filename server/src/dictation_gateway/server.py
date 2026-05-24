"""WebSocket サーバ本体と接続状態機械。

段階6-2-a スコープ:
  - 接続を受けて `ready` を送る
  - `start` / `stop` / `ping` の Text 制御メッセージを受け取り、状態機械を駆動する
  - PCM Binary フレームは受信して捨てる (段階6-2-b で riva_bridge に流す)
  - `stop` のたびに `session_end` を返す (segments=0、final/formatted は段階6-2-b 以降)
  - エラー処理:
    - 不正 JSON / Pydantic ValidationError → `BAD_MESSAGE` (recoverable=true、接続維持)
    - `protocol_version != 1` → `UNSUPPORTED_VERSION` (recoverable=false、接続クローズ)
    - 状態違反 (READY で stop など) → `BAD_MESSAGE` (recoverable=true、接続維持)
  - WebSocket レイヤの ping/pong は websockets ライブラリの ping_interval に委譲

接続状態 (dictation-gateway-protocol-v1.md §2):
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
    PingMessage,
    ReadyDefaults,
    ReadyMessage,
    ServerMessage,
    SessionEndMessage,
    StartMessage,
    StopMessage,
)

logger = logging.getLogger(__name__)

ClientAdapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
ServerAdapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)

PROTOCOL_VERSION = 1
SERVER_ID = "dictation-gateway/0.1.0"


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
    """error メッセージを送る。segment_id は省略可 (None なら exclude_unset で出力されない)。"""
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


async def _handle_text(
    ws: ServerConnection,
    state: State,
    segments_in_session: int,
    raw: str,
) -> tuple[State, int, bool]:
    """Text メッセージ 1 件を処理。戻り値は (新 state, 新 segments_in_session, keep_open)。

    keep_open=False の場合、呼び出し側がループを抜けて接続を閉じる。
    """
    try:
        parsed = ClientAdapter.validate_json(raw)
    except ValidationError as e:
        if _is_unsupported_version_error(e):
            await _send_error(
                ws,
                "UNSUPPORTED_VERSION",
                f"protocol_version must be {PROTOCOL_VERSION}",
                recoverable=False,
            )
            return state, segments_in_session, False
        await _send_error(ws, "BAD_MESSAGE", str(e), recoverable=True)
        return state, segments_in_session, True
    except json.JSONDecodeError as e:
        await _send_error(
            ws, "BAD_MESSAGE", f"invalid JSON: {e}", recoverable=True
        )
        return state, segments_in_session, True

    if isinstance(parsed, StartMessage):
        if state != State.READY:
            await _send_error(
                ws,
                "BAD_MESSAGE",
                "`start` received while not in READY state",
                recoverable=True,
            )
            return state, segments_in_session, True
        # 段階6-2-b で riva_bridge.open_stream() を呼ぶ。
        # 段階6-2-c で context フィールドを llm.py に橋渡す。
        return State.LISTENING, 0, True

    if isinstance(parsed, StopMessage):
        if state != State.LISTENING:
            await _send_error(
                ws,
                "BAD_MESSAGE",
                "`stop` received while not in LISTENING state",
                recoverable=True,
            )
            return state, segments_in_session, True
        # 段階6-2-b で riva_bridge.close_stream() → 残り partial を final/formatted に流す。
        # 段階6-2-a では即 session_end (segments=0)。
        await _send(
            ws,
            SessionEndMessage(type="session_end", segments=segments_in_session),
        )
        return State.READY, 0, True

    if isinstance(parsed, PingMessage):
        # 仕様 §4.4 / §8: WebSocket 標準 ping/pong で十分なので応答不要。
        return state, segments_in_session, True

    # 未知の variant (現状到達不能、安全側で fallback)
    await _send_error(
        ws,
        "BAD_MESSAGE",
        f"unsupported message type: {type(parsed).__name__}",
        recoverable=True,
    )
    return state, segments_in_session, True


async def _handle_binary(
    state: State, _data: bytes
) -> None:
    """Binary (PCM) フレームの処理。段階6-2-a では受信して捨てる。"""
    if state != State.LISTENING:
        # 仕様には明示されていないが、LISTENING でない時に Binary が来るのは
        # クライアント側 race のはず。エラーで切るほどではないので warning のみ。
        logger.warning("binary frame received while state=%s, dropping", state.name)
        return
    # 段階6-2-b で riva_bridge.feed_audio(_data) を呼ぶ。
    return


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

    state = State.READY
    segments_in_session = 0

    try:
        async for message in ws:
            if isinstance(message, bytes):
                await _handle_binary(state, message)
                continue

            # message は str (Text フレーム)
            new_state, new_segments, keep_open = await _handle_text(
                ws, state, segments_in_session, message
            )
            state, segments_in_session = new_state, new_segments
            if not keep_open:
                await ws.close()
                break
    except websockets.exceptions.ConnectionClosed:
        # クライアント側からの正常/異常切断はどちらも握りつぶす
        pass
    finally:
        logger.info("connection closed (peer=%s)", peer)


async def _process_request(connection, request):
    """パス制約 (/v1/dictation 固定) を WebSocket ハンドシェイク前に確認する。

    違うパスへの接続は 404 で返す。
    """
    # NOTE: connection.config はサーバ側で固定値を見るために process_request の
    # クロージャ経由で受け取りたいが、websockets.serve は process_request に
    # connection しか渡さない。サーバ起動時の path を module 変数で保持する。
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
