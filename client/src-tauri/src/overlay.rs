//! `overlay` ラベルの Tauri ウィンドウを show / hide で制御する薄いヘルパ。
//!
//! 表示仕様 (dictation-gateway-protocol-v1.md §5.2 を遵守):
//! - 同一 `segment_id` の partial は前を**置換**する (overlay 側 Svelte の責務)
//! - `final` で確定、`formatted` で LLM 整形済みに更新
//! - `session_end` の数秒後にオーバーレイを hide
//!
//! 6-3-d ではテキスト注入は行わない。overlay にだけ流す。
//! 注入は 6-3-e (inject.rs) で `formatted` を貼り付け。

use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Manager, Runtime, WebviewWindow};
use tracing::{debug, warn};

const OVERLAY_LABEL: &str = "overlay";

/// 「session_end → N 秒後に hide」の予約タスクを差し替え可能に保持する。
/// 録音再開や次の session で abort できるように。
pub type OverlayState = Mutex<Option<tauri::async_runtime::JoinHandle<()>>>;

fn window<R: Runtime>(app: &AppHandle<R>) -> Option<WebviewWindow<R>> {
    app.get_webview_window(OVERLAY_LABEL)
}

/// オーバーレイを表示する。既存の hide 予約があれば abort する。
pub fn show<R: Runtime>(app: &AppHandle<R>) {
    cancel_pending_hide(app);
    let Some(w) = window(app) else {
        warn!("overlay window not found");
        return;
    };
    if let Err(e) = w.show() {
        warn!(?e, "overlay show failed");
    }
    // focus を奪わない設定なので set_focus は呼ばない (注入先アプリの focus を保持)
    debug!("overlay shown");
}

/// オーバーレイを即時 hide。
pub fn hide<R: Runtime>(app: &AppHandle<R>) {
    cancel_pending_hide(app);
    let Some(w) = window(app) else {
        return;
    };
    if let Err(e) = w.hide() {
        warn!(?e, "overlay hide failed");
    }
    debug!("overlay hidden");
}

/// N 秒後に hide する予約。既存予約があれば差し替え。
pub fn schedule_hide<R: Runtime>(app: &AppHandle<R>, secs: u64) {
    cancel_pending_hide(app);
    let app_for_task = app.clone();
    let handle = tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_secs(secs)).await;
        if let Some(w) = app_for_task.get_webview_window(OVERLAY_LABEL) {
            let _ = w.hide();
            debug!("overlay auto-hidden after timer");
        }
    });
    if let Some(state) = app.try_state::<OverlayState>() {
        if let Ok(mut s) = state.lock() {
            *s = Some(handle);
        }
    }
}

fn cancel_pending_hide<R: Runtime>(app: &AppHandle<R>) {
    if let Some(state) = app.try_state::<OverlayState>() {
        if let Ok(mut s) = state.lock() {
            if let Some(h) = s.take() {
                h.abort();
            }
        }
    }
}
