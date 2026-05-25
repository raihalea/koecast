//! gateway への WebSocket クライアント (段階6-3-b)。
//!
//! 責務:
//! - `config.server_url` への WebSocket 接続を維持する long-lived タスクを spawn する。
//! - 接続直後のサーバ初メッセージ (`ServerMessage::Ready`) を待ち、
//!   `protocol_version == 1` を確認したら `ConnectionStatus::Ready` に遷移する。
//! - 切断 / 接続失敗 / `recoverable: false` の `error` 受信時は、
//!   1s, 2s, 4s, 8s, 16s, 30s, 30s, ... の指数バックオフで再接続する (仕様 §8)。
//! - 接続状態の変化はすべて Tauri イベント `connection-status` で UI に通知する。
//!
//! 段階6-3-b では `start` / 音声フレーム / `stop` を**送らない**。
//! `partial` / `final` / `formatted` は受信できるがログに残すだけで UI には流さない。
//! それらは 6-3-c (音声) / 6-3-d (overlay) / 6-3-e (注入) で扱う。

use std::sync::Mutex;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use koecast_protocol::ServerMessage;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tokio_tungstenite::tungstenite::Message;
use tracing::{debug, info, warn};

/// 接続状態を Tauri managed state に格納するための型 alias。
/// フロントの listener 登録が emit に間に合わず初回状態を取りこぼす問題を
/// `get_status` Tauri command で取り戻すために保持する。
pub type SharedStatus = Mutex<ConnectionStatus>;

/// 期待する protocol_version (仕様 §9)。
const EXPECTED_PROTOCOL_VERSION: i64 = 1;

/// 指数バックオフの待ち時間列 (秒)。上限 30s に張り付かせて以降は 30s 連発。
fn backoff_secs(attempt: u32) -> u64 {
    const CAP: u64 = 30;
    // attempt=1: 1, =2: 2, =3: 4, =4: 8, =5: 16, =6+: 30
    let raw = 1u64.checked_shl(attempt.saturating_sub(1)).unwrap_or(CAP);
    raw.min(CAP)
}

/// UI に流す接続状態。Svelte 側は payload をそのまま受けて表示する。
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum ConnectionStatus {
    /// 起動直後 / まだ何もしていない。
    Idle,
    /// TCP/WS ハンドシェイク中。
    Connecting { url: String },
    /// `ready` 受信完了。送受信可能 (本段階では受信のみ)。
    Ready { url: String, server: String },
    /// 切断後の待機中。
    Reconnecting {
        url: String,
        attempt: u32,
        wait_secs: u64,
        reason: String,
    },
    /// `protocol_version` 不一致など、リトライしても直らないと判明した状態。
    /// 設定を見直すまで再接続しない。
    Fatal { url: String, reason: String },
}

const EVENT: &str = "connection-status";

/// Tauri managed state を更新したうえで `connection-status` イベントを emit する。
/// listener 登録が間に合わない初回 emit を取りこぼすため、state にも書く。
fn emit(app: &AppHandle, status: &ConnectionStatus) {
    if let Some(state) = app.try_state::<SharedStatus>() {
        if let Ok(mut s) = state.lock() {
            *s = status.clone();
        }
    }
    if let Err(e) = app.emit(EVENT, status) {
        warn!(?e, "failed to emit connection-status");
    }
    debug!(?status, "connection status changed");
}

/// WebSocket クライアントを Tauri の async runtime 上に spawn する。
pub fn spawn(app: AppHandle, url: String) {
    tauri::async_runtime::spawn(async move {
        run(app, url).await;
    });
}

async fn run(app: AppHandle, url: String) {
    let mut attempt: u32 = 0;

    loop {
        attempt += 1;

        emit(
            &app,
            &ConnectionStatus::Connecting {
                url: url.clone(),
            },
        );

        match connect_and_serve(&app, &url).await {
            ConnectionOutcome::Closed(reason) => {
                let wait = backoff_secs(attempt);
                warn!(reason, attempt, wait, "WS closed; will reconnect");
                emit(
                    &app,
                    &ConnectionStatus::Reconnecting {
                        url: url.clone(),
                        attempt,
                        wait_secs: wait,
                        reason,
                    },
                );
                tokio::time::sleep(Duration::from_secs(wait)).await;
            }
            ConnectionOutcome::ReadyOk => {
                // ready まで到達できた接続が切断 → attempt をリセットして再接続
                attempt = 0;
                let wait = backoff_secs(1);
                emit(
                    &app,
                    &ConnectionStatus::Reconnecting {
                        url: url.clone(),
                        attempt: 1,
                        wait_secs: wait,
                        reason: "connection lost after ready".to_string(),
                    },
                );
                tokio::time::sleep(Duration::from_secs(wait)).await;
            }
            ConnectionOutcome::Fatal(reason) => {
                warn!(reason, "fatal protocol error; stopping reconnect");
                emit(
                    &app,
                    &ConnectionStatus::Fatal {
                        url: url.clone(),
                        reason,
                    },
                );
                return;
            }
        }
    }
}

enum ConnectionOutcome {
    /// ready に到達せず切断 (接続失敗 / 直後の切断 / 初メッセージが ready でない 等)。
    Closed(String),
    /// ready 到達後に切断。
    ReadyOk,
    /// プロトコルレベルで継続不可 (UNSUPPORTED_VERSION 等)。
    Fatal(String),
}

async fn connect_and_serve(app: &AppHandle, url: &str) -> ConnectionOutcome {
    let (mut stream, _resp) = match tokio_tungstenite::connect_async(url).await {
        Ok(v) => v,
        Err(e) => return ConnectionOutcome::Closed(format!("connect_async failed: {e}")),
    };
    info!(url, "WS connected, waiting for ready");

    let ready_outcome = wait_for_ready(&mut stream).await;
    let server_label = match ready_outcome {
        Ok(server) => server,
        Err(ReadyError::Closed(r)) => return ConnectionOutcome::Closed(r),
        Err(ReadyError::Fatal(r)) => return ConnectionOutcome::Fatal(r),
    };

    emit(
        app,
        &ConnectionStatus::Ready {
            url: url.to_string(),
            server: server_label,
        },
    );

    // ready 後の受信ループ。本段階では partial/final/formatted/session_end/error を
    // ログに流すだけ (UI への反映は 6-3-d 以降)。
    let reason = loop {
        match stream.next().await {
            Some(Ok(Message::Text(text))) => handle_server_text(text.as_ref()),
            Some(Ok(Message::Binary(_))) => debug!("ignored unexpected Binary frame from server"),
            Some(Ok(Message::Ping(_))) | Some(Ok(Message::Pong(_))) => {
                // tokio-tungstenite が ping に自動で pong を返す。何もしない。
            }
            Some(Ok(Message::Close(frame))) => {
                let r = frame
                    .map(|f| format!("close: code={}, reason={}", f.code, f.reason))
                    .unwrap_or_else(|| "close (no frame)".to_string());
                let _ = stream.send(Message::Close(None)).await;
                break r;
            }
            Some(Ok(Message::Frame(_))) => {
                // raw frame は実用上届かない。無視。
            }
            Some(Err(e)) => break format!("recv error: {e}"),
            None => break "stream ended".to_string(),
        }
    };
    info!(reason, "post-ready disconnect");
    ConnectionOutcome::ReadyOk
}

enum ReadyError {
    Closed(String),
    Fatal(String),
}

/// 接続直後の最初のメッセージが ServerMessage::Ready であることを確認する。
async fn wait_for_ready<S>(stream: &mut S) -> Result<String, ReadyError>
where
    S: StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>> + Unpin,
{
    let first = match stream.next().await {
        Some(Ok(m)) => m,
        Some(Err(e)) => return Err(ReadyError::Closed(format!("recv error before ready: {e}"))),
        None => return Err(ReadyError::Closed("stream ended before ready".to_string())),
    };

    let text = match first {
        Message::Text(t) => t,
        Message::Binary(_) => {
            return Err(ReadyError::Closed(
                "first frame was Binary, expected Text(ready)".to_string(),
            ))
        }
        Message::Close(_) => {
            return Err(ReadyError::Closed("server closed before ready".to_string()))
        }
        // ping/pong/Frame は通常 ready の前には来ないが、来たら再受信を回す
        // …とすべきだがシンプルさのため切断扱いにする。Tailscale 越しでも実害なし。
        other => {
            return Err(ReadyError::Closed(format!(
                "unexpected first frame: {other:?}"
            )))
        }
    };

    let msg: ServerMessage = match serde_json::from_str(text.as_ref()) {
        Ok(m) => m,
        Err(e) => {
            return Err(ReadyError::Closed(format!(
                "failed to parse first frame as ServerMessage: {e}"
            )))
        }
    };

    match msg {
        ServerMessage::Ready(r) => {
            if r.protocol_version != EXPECTED_PROTOCOL_VERSION {
                return Err(ReadyError::Fatal(format!(
                    "unsupported protocol_version: server={}, client expects={}",
                    r.protocol_version, EXPECTED_PROTOCOL_VERSION
                )));
            }
            info!(server = %r.server, defaults_lang = %r.defaults.language, "ready");
            Ok(r.server)
        }
        other => Err(ReadyError::Closed(format!(
            "first ServerMessage was not ready: {other:?}"
        ))),
    }
}

/// ready 後に届くサーバ → クライアントメッセージのログ出力 (6-3-b は受信のみ)。
fn handle_server_text(text: &str) {
    match serde_json::from_str::<ServerMessage>(text) {
        Ok(ServerMessage::Partial(p)) => debug!(seg = p.segment_id, text = %p.text, "partial"),
        Ok(ServerMessage::Final(f)) => info!(seg = f.segment_id, text = %f.text, "final"),
        Ok(ServerMessage::Formatted(f)) => {
            info!(seg = f.segment_id, fallback = f.fallback, text = %f.text, "formatted")
        }
        Ok(ServerMessage::SessionEnd(s)) => info!(segments = s.segments, "session_end"),
        Ok(ServerMessage::Error(e)) => {
            warn!(?e.code, msg = %e.message, recoverable = e.recoverable, "server error")
        }
        Ok(ServerMessage::Ready(_)) => {
            // ready 後に再度 ready が来るのは仕様外。ログだけ残す。
            warn!("unexpected duplicate ready after initial ready");
        }
        Err(e) => warn!(?e, raw = %text, "failed to parse server text"),
    }
}

#[cfg(test)]
mod tests {
    use super::backoff_secs;

    #[test]
    fn backoff_sequence_matches_spec() {
        // 仕様 §8: 1s, 2s, 4s, ... 上限 30s
        assert_eq!(backoff_secs(1), 1);
        assert_eq!(backoff_secs(2), 2);
        assert_eq!(backoff_secs(3), 4);
        assert_eq!(backoff_secs(4), 8);
        assert_eq!(backoff_secs(5), 16);
        assert_eq!(backoff_secs(6), 30); // 32 → 30 にクランプ
        assert_eq!(backoff_secs(7), 30);
        assert_eq!(backoff_secs(50), 30);
    }
}
