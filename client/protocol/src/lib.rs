//! Dictation Gateway WebSocket プロトコル v1 のメッセージ型 (serde)。
//!
//! protocol/schema/ の JSON Schema に厳密対応した手書きモデル。schema にない
//! フィールドや制約は付け足さない。全 struct に #[serde(deny_unknown_fields)]
//! を設定し additionalProperties: false を表現する。
//!
//! 省略可能フィールドは Option<T> + #[serde(default, skip_serializing_if = "Option::is_none")]
//! で表現し、Pydantic v2 の exclude_unset=True 相当のラウンドトリップ挙動を得る。

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum AudioEncoding {
    #[serde(rename = "LINEAR_PCM")]
    LinearPcm,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AudioFormat {
    pub sample_rate: i64,
    pub encoding: AudioEncoding,
    pub channels: i64,
}

// --- Client → Server ---

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StartMessage {
    pub protocol_version: i64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub audio: Option<AudioFormat>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub language: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub enable_formatting: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub context: Option<Vec<String>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StopMessage {}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PingMessage {}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ClientMessage {
    Start(StartMessage),
    Stop(StopMessage),
    Ping(PingMessage),
}

// --- Server → Client ---

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadyDefaults {
    pub sample_rate: i64,
    pub encoding: AudioEncoding,
    pub language: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadyMessage {
    pub protocol_version: i64,
    pub server: String,
    pub defaults: ReadyDefaults,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PartialMessage {
    pub segment_id: i64,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FinalMessage {
    pub segment_id: i64,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FormattedMessage {
    pub segment_id: i64,
    pub text: String,
    pub fallback: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SessionEndMessage {
    pub segments: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ErrorCode {
    #[serde(rename = "BAD_MESSAGE")]
    BadMessage,
    #[serde(rename = "UNSUPPORTED_VERSION")]
    UnsupportedVersion,
    #[serde(rename = "AUDIO_FORMAT_REJECTED")]
    AudioFormatRejected,
    #[serde(rename = "RIVA_UNAVAILABLE")]
    RivaUnavailable,
    #[serde(rename = "RIVA_STREAM_ERROR")]
    RivaStreamError,
    #[serde(rename = "LLM_UNAVAILABLE")]
    LlmUnavailable,
    #[serde(rename = "INTERNAL")]
    Internal,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ErrorMessage {
    pub code: ErrorCode,
    pub message: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub segment_id: Option<i64>,
    pub recoverable: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerMessage {
    Ready(ReadyMessage),
    Partial(PartialMessage),
    Final(FinalMessage),
    Formatted(FormattedMessage),
    SessionEnd(SessionEndMessage),
    Error(ErrorMessage),
}
