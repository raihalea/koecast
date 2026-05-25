#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Arc;

use koecast_client::{audio, config, hotkey, overlay, ws};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Manager, WindowEvent};
use tracing_subscriber::EnvFilter;

const MAIN_WINDOW: &str = "main";
const TRAY_ID: &str = "main-tray";

#[tauri::command]
fn get_config(state: tauri::State<'_, config::Config>) -> config::Config {
    state.inner().clone()
}

#[tauri::command]
fn get_status(state: tauri::State<'_, ws::SharedStatus>) -> ws::ConnectionStatus {
    state.lock().unwrap().clone()
}

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
            // 段階6-4-a-i: Mac で Dock にアイコンを出さない accessory アプリにする。
            // メニューバー (tray) からのみ操作する常駐アプリの形態。
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            // ws タスク (受信 + 送信ハンドル取得)
            let ws_handle = ws::spawn(app.handle().clone(), cfg_for_setup.server_url.clone());

            // マイク録音タスク (起動だけ、Start/Stop はホットキーから)
            let recorder = Arc::new(audio::AudioRecorder::new(ws_handle.clone()));

            // hotkey handler が state から取れるよう登録
            app.manage(ws_handle);
            app.manage(recorder);

            // shortcut を OS に register (handler は plugin 側で hook 済み)
            hotkey::register(&app.handle().clone());

            // main window の close を「終了」ではなく「hide」に差し替える。
            // トレイメニューの「終了」だけで明示的に exit させる設計。
            if let Some(main_window) = app.get_webview_window(MAIN_WINDOW) {
                let win_for_close = main_window.clone();
                main_window.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = win_for_close.hide();
                    }
                });
            }

            // メニューバー常駐 (tray icon + menu)
            let show_item =
                MenuItem::with_id(app, "show_settings", "設定を開く", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "終了", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &quit_item])?;

            let _tray = TrayIconBuilder::with_id(TRAY_ID)
                .icon(tauri::include_image!("icons/tray.png"))
                .icon_as_template(true) // Mac のメニューバー流儀: テンプレート画像 (アルファのみ使用)
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show_settings" => show_main_window(app),
                    "quit" => {
                        tracing::info!("quit from tray menu");
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        toggle_main_window(tray.app_handle());
                    }
                })
                .build(app)?;

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

fn show_main_window<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    if let Some(w) = app.get_webview_window(MAIN_WINDOW) {
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn toggle_main_window<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    if let Some(w) = app.get_webview_window(MAIN_WINDOW) {
        match w.is_visible() {
            Ok(true) => {
                let _ = w.hide();
            }
            _ => {
                let _ = w.show();
                let _ = w.set_focus();
            }
        }
    }
}
