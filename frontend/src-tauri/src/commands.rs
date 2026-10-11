//! Commands invoked by the Settings UI and the companion window.
//!
//! Tauri does not ACL-check app-defined commands called from a local origin (in
//! `tauri/src/webview/mod.rs` the check runs only when `plugin_command.is_some() ||
//! has_app_acl_manifest || !is_local`). Driving shortcuts, autostart and window
//! visibility through these commands keeps `capabilities/` minimal: the frontend
//! never needs the plugins' own JS permissions.

use std::sync::Mutex;

use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_autostart::ManagerExt;

use crate::settings::{PresenceSettings, settings_path};
use crate::shortcuts::{self, Registration};
use crate::windows;

/// The live settings plus the outcome of the last registration attempt, so Settings
/// can show what actually happened rather than what was asked for.
pub struct Presence {
    pub settings: Mutex<PresenceSettings>,
    pub registrations: Mutex<Vec<Registration>>,
}

impl Presence {
    pub fn new(settings: PresenceSettings) -> Self {
        Self { settings: Mutex::new(settings), registrations: Mutex::new(Vec::new()) }
    }
}

#[derive(serde::Serialize)]
pub struct PresenceState {
    settings: PresenceSettings,
    registrations: Vec<Registration>,
}

#[tauri::command]
pub fn presence_state(state: State<'_, Presence>) -> PresenceState {
    PresenceState {
        settings: state.settings.lock().unwrap().clone(),
        registrations: state.registrations.lock().unwrap().clone(),
    }
}

/// Persist new settings and apply them immediately, without a restart. Returns what
/// the OS actually granted.
#[tauri::command]
pub fn set_presence_settings(
    app: AppHandle,
    state: State<'_, Presence>,
    settings: PresenceSettings,
) -> Result<PresenceState, String> {
    let path = settings_path(app.path().app_config_dir().map_err(|e| e.to_string())?);
    // Change autostart before saving: if the OS refuses, the error returns here and
    // neither the file nor the in-memory settings claim a change that didn't happen.
    apply_autostart(&app, settings.autostart)?;
    settings.save(&path)?;

    // A refused shortcut is reported as state, not returned as an error: the UI shows
    // which combination is live and why.
    let registrations = shortcuts::apply(&app, &settings);
    *state.settings.lock().unwrap() = settings.clone();
    *state.registrations.lock().unwrap() = registrations.clone();
    Ok(PresenceState { settings, registrations })
}

fn apply_autostart(app: &AppHandle, wanted: bool) -> Result<(), String> {
    let manager = app.autolaunch();
    let enabled = manager.is_enabled().unwrap_or(false);
    if wanted && !enabled {
        manager.enable().map_err(|e| e.to_string())
    } else if !wanted && enabled {
        manager.disable().map_err(|e| e.to_string())
    } else {
        Ok(())
    }
}

#[tauri::command]
pub fn hide_companion(app: AppHandle) -> Result<(), String> {
    windows::hide_companion(&app)
}

/// Expand out of the companion into the main window. The conversation is handed over
/// by id rather than by state transfer: conversations are persisted, so the main
/// window opens the one the companion was using.
#[tauri::command]
pub fn expand_to_main(app: AppHandle, conversation_id: Option<i64>) -> Result<(), String> {
    windows::hide_companion(&app)?;
    windows::show_main(&app)?;
    app.emit_to(windows::MAIN, "veronica://open-conversation", conversation_id)
        .map_err(|e| e.to_string())
}
