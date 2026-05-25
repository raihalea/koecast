//! `formatted` テキストをアクティブウィンドウにクリップボード経由で 1 回だけ
//! ペーストする。段階6-3-e の中核。
//!
//! 安全性 (検証フェーズからの確定方針):
//! - 注入対象は **`formatted` のみ**。`partial` / `final` は注入しない。
//! - 方式は **クリップボード経由のペースト**。キーストローク方式 (pyautogui 相当) は
//!   日本語/記号で化けるので使わない。
//! - クリップボードの **元内容を退避 → 書き換え → ペースト送信 → 復元** を必ず実施。
//!   早期 return / エラー時にも復元される構造 (RAII 風の `ClipboardGuard`)。
//! - `formatted.fallback = true` (LLM 整形失敗で `final` 原文がそのまま入っている
//!   ケース) でも同じく注入する。入力を失わないための仕様 (`docs/dictation-gateway-
//!   protocol-v1.md` §5.4)。
//!
//! Mac の権限: `enigo` でキーイベントを送出するため **アクセシビリティ権限** が
//! 必要。初回ペースト時にプロンプトが出る (出なければシステム設定で手動付与)。

use std::thread;
use std::time::Duration;

use arboard::Clipboard;
use tracing::{debug, info, warn};

#[cfg(not(target_os = "macos"))]
use enigo::{Direction, Enigo, Key, Keyboard, Settings};

/// ペースト送信後にクリップボードを元に戻すまでの待機時間。
/// ペースト先アプリがクリップボードから読む前に復元してしまうと貼り付けに失敗する。
/// 150ms はローカル GUI アプリにはおおむね十分。長すぎるとクリップボード履歴系の
/// アプリが書き換え後の値を拾ってしまう副作用に注意。
const RESTORE_DELAY: Duration = Duration::from_millis(150);

/// `formatted.text` をアクティブウィンドウへ 1 回だけペーストする。
///
/// クリップボードへの一時書き込み + Cmd+V 送信 + 元クリップボードの復元を
/// 別 thread で実行する (Tauri runtime をブロックしないため)。
pub fn paste(text: String) {
    thread::Builder::new()
        .name("koecast-inject".to_string())
        .spawn(move || run_paste(text))
        .map(|_| ())
        .unwrap_or_else(|e| warn!(?e, "spawn inject thread failed"));
}

fn run_paste(text: String) {
    let mut clipboard = match Clipboard::new() {
        Ok(c) => c,
        Err(e) => {
            warn!(?e, "arboard::Clipboard::new failed; inject skipped");
            return;
        }
    };

    // 退避は失敗を許容する (もともと空 or 画像が入っている等)。
    // 復元時に saved が Some なら戻す、None なら何もしない (元が空 or 非テキスト)。
    let saved: Option<String> = clipboard.get_text().ok();

    // 書き換え → ペースト → 復元の流れ。
    // ガード型で「関数を抜けるときに必ず復元」を保証する (panic/早期 return 安全)。
    let _guard = ClipboardGuard::new(saved);

    if let Err(e) = clipboard.set_text(text.clone()) {
        warn!(?e, "clipboard set_text failed; inject skipped");
        return;
    }

    if let Err(e) = send_paste_keystroke() {
        warn!(error = %e, "send Cmd+V keystroke failed");
        // ペースト失敗でも guard で復元される
        return;
    }

    // ペースト先がクリップボードから値を読み終えるまで少し待つ
    thread::sleep(RESTORE_DELAY);

    info!(chars = text.chars().count(), "injected formatted text");
    // ここで _guard が drop されてクリップボードが元に戻る
}

/// Mac は osascript 経由でショートカットを送る。enigo の Cmd+V 直接送信は
/// 段階6-3-e の実機検証で `Key::Unicode('v')` がうまく Cmd 修飾に乗らず、
/// `Enigo::new` 後の key 送信中にアプリが強制終了する事象が出たため osascript に
/// 退避。Mac の System Events ショートカットは堅牢で、TCC アクセシビリティ権限
/// (koecast-client に付与済み) で動作する。
#[cfg(target_os = "macos")]
fn send_paste_keystroke() -> Result<(), String> {
    let status = std::process::Command::new("osascript")
        .args([
            "-e",
            r#"tell application "System Events" to keystroke "v" using command down"#,
        ])
        .status()
        .map_err(|e| format!("osascript spawn failed: {e}"))?;
    if !status.success() {
        return Err(format!("osascript exited with {status}"));
    }
    debug!("sent Cmd+V via osascript");
    Ok(())
}

#[cfg(not(target_os = "macos"))]
fn send_paste_keystroke() -> Result<(), String> {
    let mut enigo = Enigo::new(&Settings::default())
        .map_err(|e| format!("Enigo::new failed: {e:?}"))?;
    enigo.key(Key::Meta, Direction::Press).map_err(|e| format!("key press failed: {e:?}"))?;
    enigo.key(Key::Unicode('v'), Direction::Click).map_err(|e| format!("key v failed: {e:?}"))?;
    enigo.key(Key::Meta, Direction::Release).map_err(|e| format!("key release failed: {e:?}"))?;
    debug!("sent Cmd+V via enigo");
    Ok(())
}

/// drop 時にクリップボードを元の文字列に戻すガード。
/// 復元の失敗もログだけで握る (これ以上できることがない)。
struct ClipboardGuard {
    saved: Option<String>,
}

impl ClipboardGuard {
    fn new(saved: Option<String>) -> Self {
        Self { saved }
    }
}

impl Drop for ClipboardGuard {
    fn drop(&mut self) {
        let Some(prev) = self.saved.take() else {
            // 元が空 or 非テキストだったら何もしない
            return;
        };
        // ガード drop 時は別 Clipboard ハンドルを開き直す
        // (元の handle は呼び出し側で使い終わっているとは限らないため)
        match Clipboard::new() {
            Ok(mut c) => match c.set_text(prev) {
                Ok(()) => debug!("clipboard restored"),
                Err(e) => warn!(?e, "clipboard restore set_text failed"),
            },
            Err(e) => warn!(?e, "clipboard restore: Clipboard::new failed"),
        }
    }
}
