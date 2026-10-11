# Veronica front end

The React interface and the Tauri desktop shell. The backend it talks to is in
`../backend`; start it first (see the main [README](../README.md)).

```bash
npm install
npm run dev          # the interface in a browser, at http://localhost:5173
npm run tauri dev    # the desktop app (needs Rust)
```

The backend is expected at `http://localhost:8000`.

## Layout

```
src/
  pages/        Chat, Call, Voices, Memory, Settings, and the companion window
  components/   the pieces those pages are built from
  audio/        microphone capture, playback, device selection, the call hook
  chat/         the chat socket, conversations, projects, attachments
  memory/       memories, documents, learning mode
  notify/       notifications for memory changes
  kernel/       approval prompts for actions
  desktop/      the bridge to the Tauri shell
  system/       version and patch notes
  lib/          fetch helpers, routes, the router
public/audio/   the AudioWorklet that captures the microphone at 16 kHz
src-tauri/      Rust shell: tray, global hotkeys, companion window, start at login
```

Styling uses the tokens in [DESIGN.md](../DESIGN.md), wired into Tailwind in
`src/index.css`. Error text shown to the user comes from `src/lib/api.ts`, never straight
from the server.

## Checks

```bash
npm run lint         # oxlint
npx tsc -b --noEmit
npm test             # vitest
npm run build
```
