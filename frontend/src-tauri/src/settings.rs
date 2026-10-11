//! Presence settings: hotkeys, autostart, and what the window buttons do.
//!
//! Unlike the app's other settings, these are stored on the Rust side rather than in
//! the backend: the hotkeys have to work when the backend (`uvicorn`) is not running,
//! and anything behind `apiFetch` would not.
//!
//! The file is written atomically (temp file + rename). A half-written file would fail
//! to parse and silently reset the user's hotkeys to the defaults.

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

/// `Alt+Space` is what ChatGPT and Gemini use, so people already know it. On Windows
/// it is also the system window menu; registration is attempted and falls back if the
/// OS refuses (see `shortcuts`).
pub const DEFAULT_COMPANION_HOTKEY: &str = "Alt+Space";
pub const FALLBACK_COMPANION_HOTKEY: &str = "Ctrl+Alt+Space";
pub const DEFAULT_CALL_HOTKEY: &str = "Ctrl+Alt+V";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct PresenceSettings {
    pub companion_hotkey: String,
    pub call_hotkey: String,
    /// Launch at login. Off by default; the user has to opt in.
    pub autostart: bool,
    /// Closing the main window hides it instead of quitting. Quit stays in the tray
    /// menu.
    pub close_to_tray: bool,
    /// Dismiss the companion when it loses focus. On by default, since that is how a
    /// summoned panel usually behaves; some people prefer it off.
    pub dismiss_on_blur: bool,
}

impl Default for PresenceSettings {
    fn default() -> Self {
        Self {
            companion_hotkey: DEFAULT_COMPANION_HOTKEY.to_string(),
            call_hotkey: DEFAULT_CALL_HOTKEY.to_string(),
            autostart: false,
            close_to_tray: true,
            dismiss_on_blur: true,
        }
    }
}

impl PresenceSettings {
    /// Read the stored settings, falling back to the defaults if the file is missing,
    /// unreadable or unparsable. A corrupt file must not leave the app without a hotkey.
    pub fn load(path: &Path) -> Self {
        fs::read_to_string(path)
            .ok()
            .and_then(|raw| serde_json::from_str(&raw).ok())
            .unwrap_or_default()
    }

    pub fn save(&self, path: &Path) -> Result<(), String> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let body = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        // Temp + rename: an interrupted write must not leave a truncated file that
        // loads as the defaults on the next launch.
        let temp = path.with_extension("tmp");
        fs::write(&temp, body).map_err(|e| e.to_string())?;
        fs::rename(&temp, path).map_err(|e| e.to_string())
    }
}

/// `<app config dir>/presence.json`, beside the rest of the app's own state.
pub fn settings_path(config_dir: PathBuf) -> PathBuf {
    config_dir.join("presence.json")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_opt_in_and_summonable() {
        let s = PresenceSettings::default();
        assert_eq!(s.companion_hotkey, "Alt+Space");
        assert!(!s.autostart, "autostart must never default on");
        assert!(s.close_to_tray);
        assert!(s.dismiss_on_blur);
    }

    #[test]
    fn round_trips_through_disk() {
        let dir = std::env::temp_dir().join("veronica-presence-roundtrip");
        let path = dir.join("presence.json");
        let _ = fs::remove_dir_all(&dir);

        let mut written = PresenceSettings::default();
        written.companion_hotkey = "Ctrl+Shift+K".into();
        written.autostart = true;
        written.save(&path).expect("save");

        assert_eq!(PresenceSettings::load(&path), written);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_corrupt_file_falls_back_instead_of_leaving_her_unreachable() {
        let dir = std::env::temp_dir().join("veronica-presence-corrupt");
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("presence.json");
        fs::write(&path, "{ this is not json").unwrap();

        assert_eq!(PresenceSettings::load(&path), PresenceSettings::default());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_partial_file_keeps_the_defaults_for_missing_fields() {
        // `#[serde(default)]`: adding a new setting must not reset the hotkeys in an
        // existing file.
        let dir = std::env::temp_dir().join("veronica-presence-partial");
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("presence.json");
        fs::write(&path, r#"{"autostart": true}"#).unwrap();

        let loaded = PresenceSettings::load(&path);
        assert!(loaded.autostart);
        assert_eq!(loaded.companion_hotkey, DEFAULT_COMPANION_HOTKEY);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn missing_file_is_not_an_error() {
        let path = std::env::temp_dir().join("veronica-presence-nope/presence.json");
        let _ = fs::remove_file(&path);
        assert_eq!(PresenceSettings::load(&path), PresenceSettings::default());
    }
}
