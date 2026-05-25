//! gateway への WebSocket クライアント (段階6-3-b で接続維持、段階6-3-c で送信追加)。
//!
//! 責務:
//! - `config.server_url` への WebSocket 接続を維持する long-lived タスクを spawn する。
//! - 接続直後のサーバ初メッセージ (`ServerMessage::Ready`) を待ち、
//!   `protocol_version == 1` を確認したら `ConnectionStatus::Ready` に遷移する。
//! - 切断 / 接続失敗 / `recoverable: false` の `error` 受信時は、
//!   1s, 2s, 4s, 8s, 16s, 30s, 30s, ... の指数バックオフで再接続する (仕様 §8)。
//! - 接続状態の変化はすべて Tauri イベント `connection-status` で UI に通知する。
//! - 外部 (audio.rs / hotkey.rs) からの送信要求は [`WsHandle`] 経由の mpsc で受け取り、
//!   接続中の WebSocket Stream に流す。接続前/再接続中の要求はバッファせず捨てる
//!   (録音は接続が ready のときだけ意味があるので、handle 側で接続状態を見てから
//!   呼ぶこと)。
//!
//! 6-3-c で送信するのは `start` (Text) / 音声 (Binary, PCM16 LE) / `stop` (Text) の3種類。
//! `partial` / `final` / `formatted` は受信できるがログに残すだけ (UI 反映は 6-3-d 以降)。

use std::sync::Mutex;
use std::time::{Duration, Instant};

use futures_util::{SinkExt, StreamExt};
use koecast_protocol::{ClientMessage, ServerMessage, StartMessage, StopMessage};
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;
use tracing::{debug, info, warn};

/// 接続状態を Tauri managed state に格納するための型 alias。
/// フロントの listener 登録が emit に間に合わず初回状態を取りこぼす問題を
/// `get_status` Tauri command で取り戻すために保持する。
pub type SharedStatus = Mutex<ConnectionStatus>;

/// ホットキー押下からの最初の partial 受信までを計測するための保管庫。
/// 段階6-3-c の「first partial レイテンシ再測定」(計画書 §4) に使う。
pub type LatencyTracker = Mutex<Option<Instant>>;

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
    Idle,
    Connecting { url: String },
    Ready { url: String, server: String },
    Reconnecting {
        url: String,
        attempt: u32,
        wait_secs: u64,
        reason: String,
    },
    Fatal { url: String, reason: String },
}

const EVENT: &str = "connection-status";

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

// --- 外部 (audio / hotkey) からの送信要求チャネル -------------------------------

/// ws タスクへ送る要求コマンド。
pub enum WsCommand {
    /// セッション開始。仕様 §4.1 の `start` メッセージを送信する。
    SendStart(StartMessage),
    /// セッション終了。仕様 §4.3 の `stop` メッセージを送信する。
    SendStop,
    /// 音声フレーム (生 PCM16 LE)。Binary フレームで送信する。
    SendAudio(Vec<u8>),
}

/// 外部から ws タスクへ command を投げ込むハンドル。
/// `Clone` 可能で、複数モジュールに配ってよい (mpsc::UnboundedSender が cheap clone)。
#[derive(Clone)]
pub struct WsHandle {
    tx: mpsc::UnboundedSender<WsCommand>,
}

impl WsHandle {
    pub fn send_start(&self, msg: StartMessage) {
        if let Err(e) = self.tx.send(WsCommand::SendStart(msg)) {
            warn!(?e, "ws cmd_tx send_start failed (ws task dropped?)");
        }
    }

    pub fn send_stop(&self) {
        if let Err(e) = self.tx.send(WsCommand::SendStop) {
            warn!(?e, "ws cmd_tx send_stop failed (ws task dropped?)");
        }
    }

    pub fn send_audio(&self, chunk: Vec<u8>) {
        // audio chunk は秒間 10 個ペースで流れるのでログを詰まらせない。
        let _ = self.tx.send(WsCommand::SendAudio(chunk));
    }
}

/// WebSocket クライアントを Tauri の async runtime 上に spawn し、外部から命令を
/// 投げ込む [`WsHandle`] を返す。
pub fn spawn(app: AppHandle, url: String) -> WsHandle {
    let (tx, rx) = mpsc::unbounded_channel();
    let handle = WsHandle { tx };
    tauri::async_runtime::spawn(run(app, url, rx));
    handle
}

async fn run(app: AppHandle, url: String, mut cmd_rx: mpsc::UnboundedReceiver<WsCommand>) {
    let mut attempt: u32 = 0;

    loop {
        attempt += 1;

        emit(&app, &ConnectionStatus::Connecting { url: url.clone() });

        match connect_and_serve(&app, &url, &mut cmd_rx).await {
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
    Closed(String),
    ReadyOk,
    Fatal(String),
}

async fn connect_and_serve(
    app: &AppHandle,
    url: &str,
    cmd_rx: &mut mpsc::UnboundedReceiver<WsCommand>,
) -> ConnectionOutcome {
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

    // ready 前に詰まったコマンドは破棄。録音は ready 中にしか意味がない。
    while cmd_rx.try_recv().is_ok() {}

    let reason = loop {
        tokio::select! {
            biased;
            // 外部からの送信要求
            cmd = cmd_rx.recv() => {
                match cmd {
                    Some(WsCommand::SendStart(m)) => {
                        let msg = ClientMessage::Start(m);
                        if let Err(e) = send_client_message(&mut stream, &msg).await {
                            break format!("send start failed: {e}");
                        }
                        debug!("sent start");
                    }
                    Some(WsCommand::SendStop) => {
                        let msg = ClientMessage::Stop(StopMessage {});
                        if let Err(e) = send_client_message(&mut stream, &msg).await {
                            break format!("send stop failed: {e}");
                        }
                        debug!("sent stop");
                    }
                    Some(WsCommand::SendAudio(chunk)) => {
                        if let Err(e) = stream.send(Message::Binary(chunk.into())).await {
                            break format!("send audio failed: {e}");
                        }
                    }
                    None => {
                        // 全 WsHandle が drop。process 終了系。
                        break "cmd channel closed".to_string();
                    }
                }
            }
            // サーバからの受信
            frame = stream.next() => {
                match frame {
                    Some(Ok(Message::Text(text))) => handle_server_text(app, text.as_ref()),
                    Some(Ok(Message::Binary(_))) => debug!("ignored unexpected Binary frame from server"),
                    Some(Ok(Message::Ping(_))) | Some(Ok(Message::Pong(_))) => {}
                    Some(Ok(Message::Close(frame))) => {
                        let r = frame
                            .map(|f| format!("close: code={}, reason={}", f.code, f.reason))
                            .unwrap_or_else(|| "close (no frame)".to_string());
                        let _ = stream.send(Message::Close(None)).await;
                        break r;
                    }
                    Some(Ok(Message::Frame(_))) => {}
                    Some(Err(e)) => break format!("recv error: {e}"),
                    None => break "stream ended".to_string(),
                }
            }
        }
    };
    info!(reason, "post-ready disconnect");
    ConnectionOutcome::ReadyOk
}

async fn send_client_message<S>(
    stream: &mut S,
    msg: &ClientMessage,
) -> Result<(), tokio_tungstenite::tungstenite::Error>
where
    S: SinkExt<Message, Error = tokio_tungstenite::tungstenite::Error> + Unpin,
{
    let text = serde_json::to_string(msg).expect("ClientMessage serializes");
    stream.send(Message::Text(text.into())).await
}

enum ReadyError {
    Closed(String),
    Fatal(String),
}

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

/// 6-3-d: overlay ウィンドウ向けに 1 セグメント分の更新を送る。
/// kind = "partial" / "final" / "formatted"。Svelte 側は同一 segment_id の
/// partial が来たら前を**置換**する (仕様 §5.2)。
///
/// グローバル `emit` を使う (main window は segment-update を listen していない)。
/// `emit_to(label, ...)` の `From<&str> for EventTarget` 解釈で別 webview に届かない
/// ケースがあったため、シンプルに全 listener に流す方式に切り替えた。
fn emit_segment(app: &AppHandle, kind: &str, segment_id: i64, text: &str) {
    let payload = serde_json::json!({
        "kind": kind,
        "segment_id": segment_id,
        "text": text,
    });
    if let Err(e) = app.emit("segment-update", payload) {
        warn!(?e, "emit segment-update failed");
    }
}

/// ready 後に届くサーバ → クライアントメッセージのログ出力 + レイテンシ計測
/// + overlay 向け emit (6-3-d)。
fn handle_server_text(app: &AppHandle, text: &str) {
    match serde_json::from_str::<ServerMessage>(text) {
        Ok(ServerMessage::Partial(p)) => {
            emit_segment(app, "partial", p.segment_id, &p.text);
            // first partial レイテンシ計測 (計画書 §4)
            if let Some(state) = app.try_state::<LatencyTracker>() {
                if let Ok(mut t) = state.lock() {
                    if let Some(started) = t.take() {
                        let ms = started.elapsed().as_millis();
                        info!(
                            seg = p.segment_id,
                            text = %p.text,
                            latency_ms = ms,
                            "★ first partial latency"
                        );
                        return;
                    }
                }
            }
            debug!(seg = p.segment_id, text = %p.text, "partial");
        }
        Ok(ServerMessage::Final(f)) => {
            emit_segment(app, "final", f.segment_id, &f.text);
            info!(seg = f.segment_id, text = %f.text, "final");
        }
        Ok(ServerMessage::Formatted(f)) => {
            emit_segment(app, "formatted", f.segment_id, &f.text);
            info!(seg = f.segment_id, fallback = f.fallback, text = %f.text, "formatted");
            // 段階6-3-e: formatted のみアクティブウィンドウへ注入する。
            // fallback=true (LLM 失敗で final 原文) でも入力を失わないため注入する
            // (仕様 §5.4)。partial / final は overlay 表示のみ。
            crate::inject::paste(f.text);
        }
        Ok(ServerMessage::SessionEnd(s)) => {
            if let Err(e) = app.emit("segment-end", serde_json::json!({})) {
                warn!(?e, "emit segment-end failed");
            }
            crate::overlay::schedule_hide(app, 3);
            info!(segments = s.segments, "session_end");
        }
        Ok(ServerMessage::Error(e)) => {
            warn!(?e.code, msg = %e.message, recoverable = e.recoverable, "server error")
        }
        Ok(ServerMessage::Ready(_)) => {
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
