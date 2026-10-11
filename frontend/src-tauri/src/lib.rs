//! App wiring only. Each concern lives in its own module so this file stays short.

mod commands;
mod settings;
mod shortcuts;
mod tray;
mod windows;

use tauri::{Emitter, Manager, WindowEvent};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_global_shortcut::ShortcutState;

use commands::Presence;
use settings::{PresenceSettings, settings_path};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(
            // The handler routes by combination rather than by a captured id, so a
            // rebind takes effect without rebuilding the plugin.
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if event.state() != ShortcutState::Pressed {
                        return; // fire once per press, not again on release
                    }
                    // Route by what is actually registered: after a fallback the
                    // live combination is not the one in settings.
                    let live = match app.try_state::<Presence>() {
                        Some(state) => state.registrations.lock().unwrap().clone(),
                        None => return,
                    };
                    match shortcuts::action_for(&live, &shortcut.to_string()) {
                        Some(shortcuts::Action::Companion) => {
                            let _ = windows::toggle_companion(app);
                        }
                        Some(shortcuts::Action::Call) => {
                            let _ = windows::show_main(app);
                            let _ = app.emit_to(windows::MAIN, "veronica://navigate", "call");
                        }
                        None => {}
                    }
                })
                .build(),
        )
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            // Marks a launch at login, so the app can start in the tray with no
            // window. The setup hook below checks for this argument.
            Some(vec!["--autostart"]),
        ))
        .invoke_handler(tauri::generate_handler![
            commands::presence_state,
            commands::set_presence_settings,
            commands::hide_companion,
            commands::expand_to_main,
        ])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            let handle = app.handle().clone();
            let stored = PresenceSettings::load(&settings_path(app.path().app_config_dir()?));
            let registrations = shortcuts::apply(&handle, &stored);
            for registration in registrations.iter().filter(|r| !r.ok) {
                // Log failed claims: a hotkey that does nothing is otherwise invisible.
                // Settings shows the same data as user-facing copy.
                log::warn!(
                    "shortcut not registered: {} ({:?})",
                    registration.hotkey,
                    registration.code
                );
            }
            // Launched at login: stay in the tray. Hidden here rather than from the
            // frontend so the main window never flashes up first.
            if std::env::args().any(|arg| arg == "--autostart") {
                if let Some(main) = handle.get_webview_window(windows::MAIN) {
                    let _ = main.hide();
                }
            }
            let presence = Presence::new(stored);
            *presence.registrations.lock().unwrap() = registrations;
            app.manage(presence);
            tray::build(&handle)?;
            Ok(())
        })
        .on_window_event(|window, event| match event {
            // With `close_to_tray` on, closing the main window hides it instead of
            // quitting. Quit is in the tray menu.
            WindowEvent::CloseRequested { api, .. } if window.label() == windows::MAIN => {
                let close_to_tray = window
                    .app_handle()
                    .try_state::<Presence>()
                    .map(|s| s.settings.lock().unwrap().close_to_tray)
                    .unwrap_or(true);
                if close_to_tray {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
            // A summoned panel that stays up after you click away is in the way.
            WindowEvent::Focused(false) if window.label() == windows::COMPANION => {
                let dismiss = window
                    .app_handle()
                    .try_state::<Presence>()
                    .map(|s| s.settings.lock().unwrap().dismiss_on_blur)
                    .unwrap_or(true);
                if dismiss {
                    let _ = window.hide();
                }
            }
            _ => {}
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
