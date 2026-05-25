#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Arc;

use koecast_client::{audio, config, hotkey, overlay, ws};
use tauri::Manager;
use tracing_subscriber::EnvFilter;

#[tauri::command]
fn get_config(state: tauri::State<'_, config::Config>) -> config::Config {
    state.inner().clone()
}

#[tauri::command]
fn get_status(state: tauri::State<'_, ws::SharedStatus>) -> ws::ConnectionStatus {
    state.lock().unwrap().clone()
}

/// 段階6-3-f: 設定画面から呼ばれる。書き戻したファイルの絶対パス文字列を返す
/// (UI 表示用)。エラーは String 化してフロントに返す。in-memory の Config state は
/// 更新しないので、変更を効かせるにはアプリ再起動が必要 (UI で案内)。
#[tauri::command]
fn save_config(cfg: config::Config) -> Result<String, String> {
    config::save(&cfg)
        .map(|p| p.display().to_string())
        .map_err(|e| e.to_string())
}

fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| EnvFilter::new("info,koecast_client=debug")),
        )
        .init();

    let cfg = match config::load() {
        Ok(c) => c,
        Err(e) => {
            tracing::warn!(?e, "failed to load config; using defaults");
            config::Config::default()
        }
    };
    tracing::info!(
        server_url = %cfg.server_url,
        hotkey = %cfg.hotkey,
        "loaded config"
    );

    let hotkey_config = hotkey::HotkeyConfig::from_str_or_warn(&cfg.hotkey);
    let cfg_for_setup = cfg.clone();

    tauri::Builder::default()
        .plugin(hotkey::build_plugin())
        .setup(move |app| {
            // ws タスク (受信 + 送信ハンドル取得)
            let ws_handle = ws::spawn(app.handle().clone(), cfg_for_setup.server_url.clone());

            // マイク録音タスク (起動だけ、Start/Stop はホットキーから)
            let recorder = Arc::new(audio::AudioRecorder::new(ws_handle.clone()));

            // hotkey handler が state から取れるよう登録
            app.manage(ws_handle);
            app.manage(recorder);

            // shortcut を OS に register (handler は plugin 側で hook 済み)
            hotkey::register(&app.handle().clone());
            Ok(())
        })
        .manage(cfg)
        .manage(hotkey_config)
        .manage(ws::SharedStatus::new(ws::ConnectionStatus::Idle))
        .manage(ws::LatencyTracker::new(None))
        .manage(overlay::OverlayState::new(None))
        .invoke_handler(tauri::generate_handler![get_config, get_status, save_config])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
