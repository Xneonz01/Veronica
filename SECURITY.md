# Security

## Reporting a problem

Please report security problems privately, through GitHub's **Report a vulnerability**
button on this repository's Security tab, rather than in a public issue. Include what
you found, how to reproduce it, and what an attacker could do with it. Only the latest
version is supported.

## What Veronica is and isn't built to withstand

Veronica runs on one person's machine, for that person. It has no accounts and no
authentication. The backend listens on `localhost` only; do not expose it to a network.

It is designed to hold up against:

- **Web pages you visit.** A page can reach `localhost`, so every WebSocket handshake and
  every request that changes something must come from one of the app's own exact
  origins; anything else is refused. Requests addressed to any host name other than
  `localhost`, `127.0.0.1` or `[::1]` are refused before they reach a route, which stops
  a page from re-pointing its own domain at your machine (DNS rebinding) to read your data.
- **Text that tries to give orders.** Documents, files, web pages and tool results are
  passed to the model as marked, quoted data, and the model is told not to follow
  instructions inside them. Text written in characters that don't show on screen is
  removed before she reads it. Because that can never be airtight, every action that
  changes something (writing a file, opening an app) needs your approval, and silence
  counts as no; after she has read the web, so does saving a memory.
- **Pages that try to get your data out.** With web search on, she reads a page unasked
  only at an address you gave her or a search found, and asks before sending anything
  that looks like a password or a key, or words from your notes and memory. She reads
  only public addresses, never your own network or this computer, checked on every
  redirect; a page is read with limits on its size, how long it takes and how much
  work it can cause, and a search or a page read can happen only so often in a turn.
- **A permission prompt that lies.** A prompt shows where an action really goes (the
  site as it resolves, the file), marks characters that don't show on screen, says how
  much of a long value it cut and shows the rest on request, and blanks out what looks
  like a secret, saying how much it hid; asked to show all, it shows you everything,
  so nothing can hide behind a secret.
  Allow ignores clicks for its first half second.
- **Markup in what she shows.** Answers, pages and file contents are shown as text,
  never as markup, and a test fails on any code that would change that. The app's page
  carries a Content-Security-Policy that runs only the app's own code and talks only to
  her backend.
- **The model overreaching.** It can only act through a small set of capabilities, each
  with a policy, confined to the folders and apps you allow, and every decision is written
  to an append-only audit log. Veronica's own data folder, `.env` and `.git` are off-limits
  to those capabilities whatever folders you allow, and her settings and source can be
  read but never rewritten, so she can't loosen her own permissions.
- **Secrets ending up where they shouldn't.** Passwords, access codes, API keys, tokens,
  private keys and card numbers are refused by memory and by document indexing, and
  redacted from the audit log. Local diagnostics remove anything shaped like a key,
  token, password or private key, and shorten file paths so they show neither the
  account name nor where the project is installed.

Her memory file and her voice clips are encrypted at rest with keys only your Windows
account on that computer can open (SQLCipher and AES-256-GCM, ADR 0001), so a copied
folder or another account can't read them, and a stolen disk can't without your
account's password: Windows keeps the keys that protect them on the same disk, locked
with that password, so an account with no password protects nothing there, and
BitLocker is what covers the disk itself. On a system other than Windows nothing is
encrypted, and she says so. A recovery key, made in Settings > Privacy, opens them on
another computer or account too: anyone with it and a copy of her data folder can
read them, so keep it as you would a password. Like the rest of the API, making one
can be asked for by another program on this computer while she runs, which then holds
a key that opens any copy of her data folder without your Windows password, and the
one you saved stops working. Her activity log records every one made and who asked
(the app, or a program), every open window warns when a program made one, and Settings
shows when the current one was made, to the minute. It does **not** protect against software already running
as your user account, which can open the keys as she does; not everything under `data/`
is encrypted (her personality and diagnostics aren't; the files you attach in chat are,
since 0.35.0); and the audit log can be edited with a
SQLite shell by that same software: the next start finds such a change and the app says
so, but someone who knows how the log works can rewrite it to walk clean. Nor does it
keep other programs on the same computer, under any account, away from the backend while
it runs: a request that carries no browser `Origin` is taken for a local tool, so such a
program can read what the API returns (memories, conversations, the audit log, what a
permission prompt asks in full), ask for actions and answer their prompts itself, or
mark a change to the audit log as reviewed. The data folder itself is private to your account; a key held only by the
desktop app, which would close the API too, is planned. A symlink swapped between a path
check and the file being opened is not caught. The desktop shell's own permissions are
broader than the app uses.

With web search on, what you search for is seen by the search service, and a page she
reads sees the request. The check on your words leaving reads words in order, so text
that is encoded, reversed or translated gets past it, and in a search it then reaches
the search service; what keeps it from a stranger's server is that she reads unasked
only addresses you gave her or a search found. A page
can still steer what she says: a line written to her that she passes on gets a note, a
reworded one doesn't. The search service's address in Settings can be any address,
including one on your own network, and any program that can call her backend can
change it, as it can her other settings. On IPv6 she treats the /56 around this
computer as your network, which is what most homes are given; a provider that gives a
/48 leaves the rest of it looking public, as does a network that gives its computers
public IPv4 addresses.

A tool server (Settings > Tools) is a program you chose, with the rights of your
account: she asks before every one of its tools, offers a tool only once you have
approved it, and withholds it again if the server changes how it describes it, but she
can't see what the server does inside a tool, or what it sends anywhere. What it
returns is as untrusted as a web page, so after it she asks before she saves, writes or
opens anything, and as private as your own files, so a run of its words doesn't leave
unasked; a short piece of it, a code or an address, can still go out in a web search.
On Windows it is held in a job object, so it ends when she does. Any program that can call her backend can also add a server, switch
one on and approve its tools, as it can change her other settings: the same open door
as the rest of the API, which the planned key held by the desktop app closes.

[ARCHITECTURE.md §18](ARCHITECTURE.md#18-security-model) maps each defence to the code
that implements it.

## Past fixes

A full review of the codebase before the 0.9.0 release found and fixed, among others:
WebSocket endpoints that accepted any origin (any web page could have read the memory
store); state-changing "simple" POST requests that skipped the browser's CORS check;
gaps in the secrets filter (access codes, token shapes, Arabic phrasing); a path by which
deleting a memory and then editing it brought it back into the model's context; and
concurrent database writes that could commit each other's partial changes. Later
releases fixed a forgeable end-of-quote marker in tool results and a file-access
boundary that depended on the folder the server was started from. In 0.14.0, app
launching stopped searching the current directory for executables, the audit log
started screening result details for secrets and stopped storing the names of
capabilities that don't exist, debug logging no longer copies database contents to the
console, uploads and the voice list stopped exposing file paths that contained the
account name, the backend stopped trusting other localhost ports and started refusing
requests for foreign host names (DNS rebinding), and the file tools stopped being able
to read Veronica's private data or rewrite her own policy, settings and code. In 0.35.0,
with web search, a network path read by the file tools stopped sending your Windows
sign-in to another computer, the file tools stopped writing files Windows runs on its
own, permission prompts stopped being able to hide what they approve, and attached
images and uploaded documents stopped being kept unencrypted. The patch notes in
[CHANGELOG.yaml](CHANGELOG.yaml) have the details.
