{ pkgs, ... }:

{
  # --- Python (server) ---
  # 設計: server は uv で管理 (monorepo-design.md セクション4)
  languages.python = {
    enable = true;
    version = "3.14";
    uv.enable = true;
  };

  # --- Rust (client/src-tauri) ---
  languages.rust = {
    enable = true;
    channel = "stable";
  };

  # --- Node (client フロントエンド) ---
  # 設計: Svelte + Vite + Tauri 2.x (monorepo-design.md セクション5)
  languages.javascript = {
    enable = true;
    package = pkgs.nodejs_24;
    npm.enable = true;
  };

  # --- タスクランナー ---
  packages = with pkgs; [
    just
  ];

  # NOTE(stage-3+):
  # Tauri が要求する OS 依存 (WebKitGTK / libsoup / WebView2 等) は
  # monorepo-design.md セクション10 で未確定。実機ビルド時に追記する。
}
