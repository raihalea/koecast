// 段階6-3-b: config 読み込みと WebSocket クライアントを公開。
// audio / hotkey / inject / overlay は 6-3-c 以降で順次実装する (現状は空モジュール)。

pub mod config;
pub mod ws;

pub mod audio;
pub mod hotkey;
pub mod inject;
pub mod overlay;
