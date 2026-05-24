//! protocol/examples/ のゴールデン例に対する契約テスト (client 側 / serde 版)。
//!
//! 各 example について以下を検証する:
//!   1. JSON Schema (Draft 2020-12) に対して valid
//!   2. serde でデシリアライズ成功
//!   3. 再シリアライズして serde_json::Value 同士で構造的等価
//!
//! さらに論点2 の確認として、deny_unknown_fields が tag=\"type\" の Union 配下でも
//! 機能すること (未知フィールド・未知 type が弾かれること) を negative テストで確認する。

use std::path::PathBuf;

use jsonschema::Validator;
use koecast_client::protocol::{ClientMessage, ServerMessage};
use serde::{de::DeserializeOwned, Serialize};
use serde_json::Value;

fn protocol_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../protocol")
}

fn load_example(name: &str) -> Value {
    let path = protocol_dir().join("examples").join(name);
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("read {}: {}", path.display(), e));
    serde_json::from_str(&text)
        .unwrap_or_else(|e| panic!("parse {}: {}", path.display(), e))
}

fn compile_schema(name: &str) -> Validator {
    let path = protocol_dir().join("schema").join(name);
    let text = std::fs::read_to_string(&path).unwrap();
    let schema: Value = serde_json::from_str(&text).unwrap();
    jsonschema::validator_for(&schema)
        .unwrap_or_else(|e| panic!("compile schema {}: {}", name, e))
}

fn assert_schema_valid(value: &Value, validator: &Validator, name: &str) {
    if !validator.is_valid(value) {
        let errors: Vec<String> = validator
            .iter_errors(value)
            .map(|e| format!("{} @ {}", e, e.instance_path))
            .collect();
        panic!("{} fails JSON Schema:\n  {}", name, errors.join("\n  "));
    }
}

fn assert_roundtrip<T>(original: &Value, name: &str)
where
    T: DeserializeOwned + Serialize,
{
    let parsed: T = serde_json::from_value(original.clone())
        .unwrap_or_else(|e| panic!("deserialize {}: {}", name, e));
    let redumped: Value = serde_json::to_value(&parsed)
        .unwrap_or_else(|e| panic!("serialize {}: {}", name, e));
    assert_eq!(
        *original, redumped,
        "drift in {}\n  original: {}\n  redumped: {}",
        name, original, redumped
    );
}

const CLIENT_EXAMPLES: &[&str] = &[
    "start.json",
    "start_min.json",
    "stop.json",
    "ping.json",
];

const SERVER_EXAMPLES: &[&str] = &[
    "ready.json",
    "partial.json",
    "final.json",
    "formatted.json",
    "session_end.json",
    "error.json",
    "error_with_segment.json",
];

#[test]
fn client_examples_pass_schema_and_roundtrip() {
    let validator = compile_schema("client-to-server.schema.json");
    for name in CLIENT_EXAMPLES {
        let example = load_example(name);
        assert_schema_valid(&example, &validator, name);
        assert_roundtrip::<ClientMessage>(&example, name);
    }
}

#[test]
fn server_examples_pass_schema_and_roundtrip() {
    let validator = compile_schema("server-to-client.schema.json");
    for name in SERVER_EXAMPLES {
        let example = load_example(name);
        assert_schema_valid(&example, &validator, name);
        assert_roundtrip::<ServerMessage>(&example, name);
    }
}

// --- 論点2 の共存確認: tag="type" Union + deny_unknown_fields ---

#[test]
fn deny_unknown_fields_rejects_extra_in_start() {
    let bad = serde_json::json!({
        "type": "start",
        "protocol_version": 1,
        "bogus_field": "should be rejected"
    });
    let result: Result<ClientMessage, _> = serde_json::from_value(bad);
    assert!(
        result.is_err(),
        "start に未知フィールドがあるのに通った: {:?}",
        result
    );
}

#[test]
fn deny_unknown_fields_rejects_extra_in_error() {
    let bad = serde_json::json!({
        "type": "error",
        "code": "INTERNAL",
        "message": "x",
        "recoverable": false,
        "rogue": 42
    });
    let result: Result<ServerMessage, _> = serde_json::from_value(bad);
    assert!(
        result.is_err(),
        "error に未知フィールドがあるのに通った: {:?}",
        result
    );
}

#[test]
fn unknown_type_is_rejected_on_client_side() {
    let bad = serde_json::json!({ "type": "telepathy" });
    let result: Result<ClientMessage, _> = serde_json::from_value(bad);
    assert!(
        result.is_err(),
        "未知の type が通った: {:?}",
        result
    );
}

#[test]
fn unknown_type_is_rejected_on_server_side() {
    let bad = serde_json::json!({ "type": "telepathy", "segment_id": 1, "text": "x" });
    let result: Result<ServerMessage, _> = serde_json::from_value(bad);
    assert!(
        result.is_err(),
        "未知の type が通った: {:?}",
        result
    );
}

// 念のため: 既知の type の中で type 自体が enum タグとして消費されていることを確認。
// (deny_unknown_fields が type を unknown と誤検知しないことの確認)
#[test]
fn known_type_is_not_treated_as_unknown_by_inner_struct() {
    let minimal_start = serde_json::json!({
        "type": "start",
        "protocol_version": 1
    });
    let parsed: ClientMessage = serde_json::from_value(minimal_start)
        .expect("type タグ消費が壊れていて start_min が落ちた");
    match parsed {
        ClientMessage::Start(_) => (),
        other => panic!("type=start なのに別 variant にパースされた: {:?}", other),
    }
}
