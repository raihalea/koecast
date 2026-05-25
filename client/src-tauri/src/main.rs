#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use koecast_client::{config, ws};
use tracing_subscriber::EnvFilter;

#[tauri::command]
fn get_config(state: tauri::State<'_, config::Config>) -> config::Config {
    state.inner().clone()
}

#[tauri::command]
fn get_status(state: tauri::State<'_, ws::SharedStatus>) -> ws::ConnectionStatus {
    state.lock().unwrap().clone()
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
    tracing::info!(server_url = %cfg.server_url, "loaded config");

    let cfg_for_setup = cfg.clone();
    tauri::Builder::default()
        .setup(move |app| {
            ws::spawn(app.handle().clone(), cfg_for_setup.server_url.clone());
            Ok(())
        })
        .manage(cfg)
        .manage(ws::SharedStatus::new(ws::ConnectionStatus::Idle))
        .invoke_handler(tauri::generate_handler![get_config, get_status])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
