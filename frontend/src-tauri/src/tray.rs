//! The tray icon and menu, which keep the app reachable when no window is open.
//!
//! The tray is what makes hiding the main window on close safe: it is the way back
//! to a hidden window and the place to actually quit.

use tauri::menu::{Menu, MenuEvent, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter};

use crate::windows;

pub const OPEN: &str = "open";
pub const CALL: &str = "call";
pub const SETTINGS: &str = "settings";
pub const QUIT: &str = "quit";

pub fn build(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, OPEN, "Open Veronica", true, None::<&str>)?;
    let call = MenuItem::with_id(app, CALL, "Start a call", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, SETTINGS, "Settings", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, QUIT, "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &call, &settings, &quit])?;

    TrayIconBuilder::with_id("veronica")
        .icon(app.default_window_icon().cloned().expect("bundled icon"))
        .tooltip("Veronica")
        .menu(&menu)
        // Left click toggles the companion, like the hotkey: the tray icon is the
        // discoverable form of the shortcut.
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if let tauri::tray::TrayIconEvent::Click {
                button: tauri::tray::MouseButton::Left,
                button_state: tauri::tray::MouseButtonState::Up,
                ..
            } = event
            {
                let _ = windows::toggle_companion(tray.app_handle());
            }
        })
        .on_menu_event(on_menu)
        .build(app)?;
    Ok(())
}

fn on_menu(app: &AppHandle, event: MenuEvent) {
    match event.id().as_ref() {
        OPEN => {
            let _ = windows::show_main(app);
        }
        CALL => {
            let _ = windows::show_main(app);
            // Navigation belongs to the frontend; Rust only emits the target route.
            let _ = app.emit_to(windows::MAIN, "veronica://navigate", "call");
        }
        SETTINGS => {
            let _ = windows::show_main(app);
            let _ = app.emit_to(windows::MAIN, "veronica://navigate", "settings");
        }
        QUIT => {
            // Release the hotkeys before exiting so other apps can claim the
            // combinations again.
            crate::shortcuts::release_all(app);
            app.exit(0);
        }
        _ => {}
    }
}
