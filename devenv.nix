{ pkgs, ... }:

{
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

  # NOTE: Python は devenv では管理しない。
  # server/pyproject.toml の requires-python に従って uv が自前で Python ランタイムを
  # 調達する (nixpkgs-python の上流不整合を避けつつ、言語専用マネージャに責務を集約)。
  #
  # NOTE(stage-3+):
  # Tauri が要求する OS 依存 (WebKitGTK / libsoup / WebView2 等) は
  # monorepo-design.md セクション10 で未確定。実機ビルド時に追記する。
}
