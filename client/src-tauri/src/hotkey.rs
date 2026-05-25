//! グローバルホットキー (push-to-talk) ハンドラ。
//!
//! 押下: `start` 送信 + 録音開始 + レイテンシ計測 (`LatencyTracker` に Instant::now())
//! 解放: `stop` 送信 + 録音停止 + tracker クリア
//!
//! `tauri-plugin-global-shortcut` の Pressed/Released イベントを使うため、
//! macOS では "入力監視 (Input Monitoring)" 権限が必要 (docs/client-setup.md §2.4)。
//!
//! 設計メモ: plugin builder と setup() でデータの受け渡しを綺麗にするため、
//! WsHandle / AudioRecorder / HotkeyConfig はすべて Tauri State 経由で取得する。
//! plugin 自体は `build_plugin()` で「state を参照する handler」だけを持つ。

use std::str::FromStr;
use std::sync::Arc;
use std::time::Instant;

use koecast_protocol::StartMessage;
use tauri::{AppHandle, Manager, Runtime};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};
use tracing::{info, warn};

use crate::audio::AudioRecorder;
use crate::ws::{LatencyTracker, WsHandle};

/// Tauri State として manage されるホットキー設定。設定が無効 (parse 失敗) なら
/// `shortcut` が None で、handler は全イベントを無視する。
pub struct HotkeyConfig {
    pub shortcut: Option<Shortcut>,
}

impl HotkeyConfig {
    pub fn from_str_or_warn(s: &str) -> Self {
        match Shortcut::from_str(s) {
            Ok(sh) => Self { shortcut: Some(sh) },
            Err(e) => {
                warn!(?e, hotkey_str = s, "failed to parse hotkey; push-to-talk disabled");
                Self { shortcut: None }
            }
        }
    }
}

/// `tauri::Builder::default().plugin(...)` に渡す global-shortcut プラグイン。
/// 必要なデータは handler 内で `app.state()` から取る。
pub fn build_plugin<R: Runtime>() -> tauri::plugin::TauriPlugin<R> {
    tauri_plugin_global_shortcut::Builder::new()
        .with_handler(|app, shortcut, event| {
            let cfg = app.state::<HotkeyConfig>();
            let Some(ref expected) = cfg.shortcut else {
                return;
            };
            if shortcut != expected {
                return;
            }
            match event.state() {
                ShortcutState::Pressed => on_pressed(app),
                ShortcutState::Released => on_released(app),
            }
        })
        .build()
}

/// `app.global_shortcut().register(shortcut)` を呼ぶ。setup() の最後に呼ぶ想定。
pub fn register<R: Runtime>(app: &AppHandle<R>) {
    let cfg = app.state::<HotkeyConfig>();
    let Some(ref shortcut) = cfg.shortcut else {
        warn!("hotkey not configured; skip register");
        return;
    };
    match app.global_shortcut().register(shortcut.clone()) {
        Ok(()) => info!(?shortcut, "registered global hotkey"),
        Err(e) => warn!(?e, "global_shortcut.register failed"),
    }
}

fn on_pressed<R: Runtime>(app: &AppHandle<R>) {
    info!("hotkey pressed -> start recording");

    if let Some(state) = app.try_state::<LatencyTracker>() {
        if let Ok(mut t) = state.lock() {
            *t = Some(Instant::now());
        }
    }

    let ws = app.state::<WsHandle>();
    let start_msg = StartMessage {
        protocol_version: 1,
        audio: None,
        language: None,
        enable_formatting: None,
        context: None,
    };
    ws.send_start(start_msg);

    let recorder = app.state::<Arc<AudioRecorder>>();
    recorder.start();
}

fn on_released<R: Runtime>(app: &AppHandle<R>) {
    info!("hotkey released -> stop recording");

    let recorder = app.state::<Arc<AudioRecorder>>();
    recorder.stop();

    let ws = app.state::<WsHandle>();
    ws.send_stop();

    if let Some(state) = app.try_state::<LatencyTracker>() {
        if let Ok(mut t) = state.lock() {
            *t = None;
        }
    }
}
