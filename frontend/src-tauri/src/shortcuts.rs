//! Global shortcuts that summon the app from anywhere.
//!
//! Registration can fail: another app may already own the combination, and on
//! Windows `Alt+Space` is the system window menu. A hotkey that silently does
//! nothing looks like a broken app, so every attempt returns a typed outcome that
//! Settings shows, and the companion hotkey falls back to a second combination
//! rather than leaving the user no way to summon it.

use serde::{Deserialize, Serialize};
use tauri::AppHandle;
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut};

use crate::settings::{FALLBACK_COMPANION_HOTKEY, PresenceSettings};

/// Which action a registered combination triggers.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Action {
    Companion,
    Call,
}

/// The outcome of claiming a combination. Sent to the Settings UI as-is, which turns
/// it into user-facing copy.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Registration {
    pub action: Action,
    /// The combination claimed (or attempted, when `ok` is false). After a fallback
    /// this is not the one that was requested.
    pub hotkey: String,
    pub ok: bool,
    /// Set when `hotkey` is not what was asked for, so the UI can say why.
    pub requested: Option<String>,
    /// A machine-readable reason. The frontend maps it to copy and never shows it raw.
    pub code: Option<String>,
}

impl Registration {
    fn ok(action: Action, hotkey: &str) -> Self {
        Self { action, hotkey: hotkey.into(), ok: true, requested: None, code: None }
    }
}

/// Validate a combination before asking the OS, so Settings can give a specific reason
/// instead of surfacing a plugin's parse error.
pub fn parse(hotkey: &str) -> Result<Shortcut, String> {
    let trimmed = hotkey.trim();
    if trimmed.is_empty() {
        return Err("empty".into());
    }
    // A bare key with no modifier would swallow that key everywhere on the machine.
    if !trimmed.contains('+') {
        return Err("no_modifier".into());
    }
    trimmed.parse::<Shortcut>().map_err(|_| "unparseable".into())
}

/// Claim both combinations. Called at startup and again on every rebind, so a change
/// takes effect without a restart.
pub fn apply(app: &AppHandle, settings: &PresenceSettings) -> Vec<Registration> {
    let manager = app.global_shortcut();
    // Drop everything first: rebinding must not leave the old combination claimed,
    // and re-registering an already-held shortcut is an error on some platforms.
    let _ = manager.unregister_all();

    let companion = claim_with_fallback(app, Action::Companion, &settings.companion_hotkey);
    // A call hotkey equal to the live companion hotkey is reported as such, not as
    // `already_taken`, which would send the user looking for a conflicting app.
    let call = if companion.ok && companion.hotkey.eq_ignore_ascii_case(&settings.call_hotkey) {
        Registration {
            action: Action::Call,
            hotkey: settings.call_hotkey.clone(),
            ok: false,
            requested: None,
            code: Some("same_as_companion".into()),
        }
    } else {
        claim(app, Action::Call, &settings.call_hotkey)
    };
    vec![companion, call]
}

/// Claim the companion hotkey, falling back to `FALLBACK_COMPANION_HOTKEY` if it is
/// refused, so the companion stays reachable and `requested` explains the switch.
fn claim_with_fallback(app: &AppHandle, action: Action, hotkey: &str) -> Registration {
    let first = claim(app, action, hotkey);
    if first.ok || hotkey.eq_ignore_ascii_case(FALLBACK_COMPANION_HOTKEY) {
        return first;
    }
    let mut second = claim(app, action, FALLBACK_COMPANION_HOTKEY);
    second.requested = Some(hotkey.to_string());
    if second.ok {
        second.code = Some("fell_back".into());
    }
    second
}

fn claim(app: &AppHandle, action: Action, hotkey: &str) -> Registration {
    let shortcut = match parse(hotkey) {
        Ok(shortcut) => shortcut,
        Err(code) => {
            return Registration {
                action,
                hotkey: hotkey.into(),
                ok: false,
                requested: None,
                code: Some(code),
            };
        }
    };
    match app.global_shortcut().register(shortcut) {
        Ok(()) => Registration::ok(action, hotkey),
        Err(_) => Registration {
            action,
            hotkey: hotkey.into(),
            ok: false,
            requested: None,
            // Almost always another application already holding the combination.
            code: Some("already_taken".into()),
        },
    }
}

/// Release everything on quit, so another app can use the combination again.
pub fn release_all(app: &AppHandle) {
    let _ = app.global_shortcut().unregister_all();
}

/// Map a fired shortcut to its action.
///
/// Routes against the live registrations, not the settings: settings record what was
/// asked for, and after a fallback the two differ. Treating the fallback combination
/// as always meaning the companion would hijack a call hotkey the user bound to it.
pub fn action_for(registrations: &[Registration], fired: &str) -> Option<Action> {
    registrations
        .iter()
        .find(|r| r.ok && r.hotkey.eq_ignore_ascii_case(fired))
        .map(|r| r.action)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_bare_key_is_refused() {
        // Registering "Space" globally would eat the space bar everywhere.
        assert_eq!(parse("Space"), Err("no_modifier".into()));
        assert_eq!(parse("A"), Err("no_modifier".into()));
    }

    #[test]
    fn empty_input_is_refused() {
        assert_eq!(parse(""), Err("empty".into()));
        assert_eq!(parse("   "), Err("empty".into()));
    }

    #[test]
    fn nonsense_is_refused_before_it_reaches_the_os() {
        assert_eq!(parse("Ctrl+NotAKey"), Err("unparseable".into()));
    }

    #[test]
    fn the_shipped_defaults_and_fallback_all_parse() {
        for hotkey in ["Alt+Space", "Ctrl+Alt+Space", "Ctrl+Alt+V", "Ctrl+Shift+K"] {
            assert!(parse(hotkey).is_ok(), "{hotkey} should parse");
        }
    }

    fn registered(action: Action, hotkey: &str) -> Registration {
        Registration::ok(action, hotkey)
    }

    #[test]
    fn a_fired_combination_routes_to_the_right_action() {
        let live = [
            registered(Action::Companion, "Alt+Space"),
            registered(Action::Call, "Ctrl+Alt+V"),
        ];
        assert_eq!(action_for(&live, "Alt+Space"), Some(Action::Companion));
        assert_eq!(action_for(&live, "Ctrl+Alt+V"), Some(Action::Call));
        assert_eq!(action_for(&live, "Ctrl+Q"), None);
    }

    #[test]
    fn the_fallback_still_summons_her() {
        // After a fallback the live combination differs from the one in settings; it
        // must still route, or the fallback would register and then do nothing.
        let live = [registered(Action::Companion, FALLBACK_COMPANION_HOTKEY)];
        assert_eq!(
            action_for(&live, FALLBACK_COMPANION_HOTKEY),
            Some(Action::Companion)
        );
    }

    #[test]
    fn a_rebound_hotkey_routes_and_the_old_one_stops() {
        let live = [registered(Action::Companion, "Ctrl+Shift+K")];
        assert_eq!(action_for(&live, "Ctrl+Shift+K"), Some(Action::Companion));
        assert_eq!(action_for(&live, "Alt+Space"), None);
    }

    #[test]
    fn the_fallback_combination_does_not_hijack_a_deliberate_call_binding() {
        // A call bound to `Ctrl+Alt+Space` must start a call, even though that is
        // also the companion's fallback combination.
        let live = [
            registered(Action::Companion, "Alt+Space"),
            registered(Action::Call, FALLBACK_COMPANION_HOTKEY),
        ];
        assert_eq!(action_for(&live, FALLBACK_COMPANION_HOTKEY), Some(Action::Call));
    }

    #[test]
    fn a_combination_that_failed_to_register_routes_nowhere() {
        // A failed registration is not live, so it must never match a fired
        // combination.
        let mut failed = registered(Action::Call, "Ctrl+Alt+V");
        failed.ok = false;
        assert_eq!(action_for(&[failed], "Ctrl+Alt+V"), None);
    }

    #[test]
    fn her_two_shortcuts_colliding_is_reported_as_itself() {
        // A call hotkey that duplicates the companion's is reported as
        // `same_as_companion`, not as taken by another app, and the companion keeps
        // the combination.
        let live = [
            Registration::ok(Action::Companion, "Alt+Space"),
            Registration {
                action: Action::Call,
                hotkey: "Alt+Space".into(),
                ok: false,
                requested: None,
                code: Some("same_as_companion".into()),
            },
        ];
        assert_eq!(action_for(&live, "Alt+Space"), Some(Action::Companion));
    }
}
