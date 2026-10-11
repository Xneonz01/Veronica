//! Summoning and dismissing the companion window, and bringing up the main one.
//!
//! The companion window is declared in `tauri.conf.json` and created hidden at
//! startup, then only shown and hidden here. Creating it per press would boot a
//! webview every time, which shows up as a visible stutter.

use tauri::{AppHandle, Emitter, Manager, WebviewWindow};

pub const COMPANION: &str = "companion";
pub const MAIN: &str = "main";

/// What a summon request should do, given the window's current state.
///
/// Kept as a pure function so the toggle behaviour is testable without a webview.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Summon {
    Show,
    Dismiss,
}

pub fn decide(visible: bool, focused: bool) -> Summon {
    // Visible but unfocused means another window took focus while the companion
    // stayed on top: the user is asking for it, not dismissing it.
    if visible && focused {
        Summon::Dismiss
    } else {
        Summon::Show
    }
}

/// Toggle the companion: show + centre + focus, or hide.
pub fn toggle_companion(app: &AppHandle) -> Result<(), String> {
    let window = companion(app)?;
    let visible = window.is_visible().unwrap_or(false);
    let focused = window.is_focused().unwrap_or(false);
    match decide(visible, focused) {
        Summon::Dismiss => hide_companion(app),
        Summon::Show => show_companion(app),
    }
}

pub fn show_companion(app: &AppHandle) -> Result<(), String> {
    let window = companion(app)?;
    // Centre before showing so the window never appears at its old position and
    // jumps. `center()` uses the monitor the window is currently on, which in
    // practice is the active display.
    let _ = window.center();
    window.show().map_err(|e| e.to_string())?;
    window.set_focus().map_err(|e| e.to_string())?;
    // The frontend focuses the input on this event; without it the user would have
    // to click into the window before typing.
    let _ = window.emit("companion://summoned", ());
    Ok(())
}

pub fn hide_companion(app: &AppHandle) -> Result<(), String> {
    let window = companion(app)?;
    window.hide().map_err(|e| e.to_string())
}

/// Bring the main window up (tray "Open", and expanding out of the companion).
pub fn show_main(app: &AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window(MAIN)
        .ok_or_else(|| "the main window is not available".to_string())?;
    window.show().map_err(|e| e.to_string())?;
    let _ = window.unminimize();
    window.set_focus().map_err(|e| e.to_string())
}

fn companion(app: &AppHandle) -> Result<WebviewWindow, String> {
    app.get_webview_window(COMPANION)
        .ok_or_else(|| "the companion window is not available".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pressing_the_hotkey_while_she_is_up_dismisses_her() {
        assert_eq!(decide(true, true), Summon::Dismiss);
    }

    #[test]
    fn pressing_it_while_hidden_summons_her() {
        assert_eq!(decide(false, false), Summon::Show);
    }

    #[test]
    fn visible_but_unfocused_is_a_request_for_her_not_a_dismissal() {
        // The companion is always-on-top, so it can be on screen while another app
        // has focus. Pressing the hotkey then means "bring it back", not "hide it".
        assert_eq!(decide(true, false), Summon::Show);
    }
}
