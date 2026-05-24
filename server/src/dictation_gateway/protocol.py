"""Dictation Gateway WebSocket プロトコル v1 のメッセージ型 (Pydantic v2)。

protocol/schema/ の JSON Schema に厳密対応した手書きモデル。schema にないフィールドや
制約は付け足さない。全 object に extra="forbid" を設定して additionalProperties: false
を表現する。
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


AudioEncoding = Literal["LINEAR_PCM"]


class AudioFormat(_Strict):
    sample_rate: int
    encoding: AudioEncoding
    channels: int


class StartMessage(_Strict):
    type: Literal["start"]
    protocol_version: Literal[1]
    audio: Optional[AudioFormat] = None
    language: Optional[str] = None
    enable_formatting: Optional[bool] = None
    context: Optional[list[str]] = None


class StopMessage(_Strict):
    type: Literal["stop"]


class PingMessage(_Strict):
    type: Literal["ping"]


ClientMessage = Annotated[
    Union[StartMessage, StopMessage, PingMessage],
    Field(discriminator="type"),
]


class ReadyDefaults(_Strict):
    sample_rate: int
    encoding: AudioEncoding
    language: str


class ReadyMessage(_Strict):
    type: Literal["ready"]
    protocol_version: Literal[1]
    server: str
    defaults: ReadyDefaults


class PartialMessage(_Strict):
    type: Literal["partial"]
    segment_id: int
    text: str


class FinalMessage(_Strict):
    type: Literal["final"]
    segment_id: int
    text: str


class FormattedMessage(_Strict):
    type: Literal["formatted"]
    segment_id: int
    text: str
    fallback: bool


class SessionEndMessage(_Strict):
    type: Literal["session_end"]
    segments: int


ErrorCode = Literal[
    "BAD_MESSAGE",
    "UNSUPPORTED_VERSION",
    "AUDIO_FORMAT_REJECTED",
    "RIVA_UNAVAILABLE",
    "RIVA_STREAM_ERROR",
    "LLM_UNAVAILABLE",
    "INTERNAL",
]


class ErrorMessage(_Strict):
    type: Literal["error"]
    code: ErrorCode
    message: str
    segment_id: Optional[int] = None
    recoverable: bool


ServerMessage = Annotated[
    Union[
        ReadyMessage,
        PartialMessage,
        FinalMessage,
        FormattedMessage,
        SessionEndMessage,
        ErrorMessage,
    ],
    Field(discriminator="type"),
]
