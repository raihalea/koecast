//! koecast クライアント設定の読み込み。
//!
//! 優先順位: 設定ファイル > デフォルト。
//! 設定ファイル探索:
//!   1. 環境変数 KOECAST_CLIENT_CONFIG=<path> が指定されていればそれ
//!   2. OS 標準の config dir (Mac: ~/Library/Application Support, Win: %APPDATA%) 配下の
//!      `koecast/config.toml`
//!   3. なければデフォルト値のみ
//!
//! 設定ファイルは TOML。フラットなキーで Config の構造体フィールドに対応:
//!
//!   server_url = "ws://dgx.tail-xxxx.ts.net:8000/v1/dictation"
//!   hotkey = "CmdOrCtrl+Alt+Space"      # 段階6-3-c で global-hotkey に渡す
//!   glossary = ["ThreatLens", "Bedrock"]  # 段階6-2-c で start.context として渡す
//!
//! 段階6-3-b で実用するのは `server_url` のみ。`hotkey` と `glossary` は
//! 後続段階で読み出すための定義先取り。

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    /// gateway の WebSocket URL。Tailscale MagicDNS 名 + port + path。
    pub server_url: String,

    /// global-hotkey 形式のホットキー記述。段階6-3-c で初使用。
    pub hotkey: String,

    /// セッション単位の Word Boosting 候補語。段階6-3-c 以降で `start.context` に
    /// 詰めて送る。常用辞書はサーバ側保持なのでここに重複させない。
    pub glossary: Vec<String>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            server_url: "ws://localhost:8000/v1/dictation".to_string(),
            hotkey: "CmdOrCtrl+Alt+Space".to_string(),
            glossary: Vec::new(),
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("failed to read {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("failed to parse {path}: {source}")]
    Parse {
        path: PathBuf,
        #[source]
        source: toml::de::Error,
    },
}

/// 設定ファイルの探索結果を読み、無ければデフォルトを返す。
///
/// 探索 path の解決ロジックは [`resolve_config_path`] に分離してあるので、
/// テスト時は [`load_from_path`] を直接呼べる。
pub fn load() -> Result<Config, ConfigError> {
    match resolve_config_path() {
        Some(path) if path.is_file() => load_from_path(&path),
        _ => Ok(Config::default()),
    }
}

pub fn load_from_path(path: &Path) -> Result<Config, ConfigError> {
    let text = std::fs::read_to_string(path).map_err(|source| ConfigError::Io {
        path: path.to_path_buf(),
        source,
    })?;
    toml::from_str(&text).map_err(|source| ConfigError::Parse {
        path: path.to_path_buf(),
        source,
    })
}

/// 設定ファイルの絶対 path を返す。環境変数優先、無ければ OS 標準 config dir。
pub fn resolve_config_path() -> Option<PathBuf> {
    if let Some(env_path) = std::env::var_os("KOECAST_CLIENT_CONFIG") {
        return Some(PathBuf::from(env_path));
    }
    dirs::config_dir().map(|d| d.join("koecast").join("config.toml"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_url_is_localhost() {
        let c = Config::default();
        assert!(c.server_url.starts_with("ws://localhost"));
    }

    #[test]
    fn load_from_path_parses_minimal_toml() {
        let dir = unique_dir("parses_minimal");
        let path = dir.join("c.toml");
        std::fs::write(
            &path,
            r#"
server_url = "ws://example.test:9000/v1/dictation"
hotkey = "F12"
glossary = ["foo", "bar"]
"#,
        )
        .unwrap();
        let c = load_from_path(&path).unwrap();
        assert_eq!(c.server_url, "ws://example.test:9000/v1/dictation");
        assert_eq!(c.hotkey, "F12");
        assert_eq!(c.glossary, vec!["foo".to_string(), "bar".to_string()]);
    }

    #[test]
    fn missing_keys_fall_back_to_defaults() {
        let dir = unique_dir("missing_keys");
        let path = dir.join("c.toml");
        std::fs::write(&path, r#"server_url = "ws://only-this.test/v1/dictation""#).unwrap();
        let c = load_from_path(&path).unwrap();
        assert_eq!(c.server_url, "ws://only-this.test/v1/dictation");
        assert_eq!(c.hotkey, Config::default().hotkey);
        assert!(c.glossary.is_empty());
    }

    fn unique_dir(tag: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "koecast-config-test-{}-{tag}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        p
    }
}
