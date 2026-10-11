# tidepool

tidepool copies photo albums from a laptop to a network share and keeps them in step. It only ever adds and updates files on the share; it never deletes a photo on either side unless it is told to with a flag.

## Installing

tidepool needs Python 3.11 or newer. Install it with pipx so it gets an environment of its own:

    pipx install tidepool-sync

Check the install with `tidepool --version`. The first run writes a starter settings file to `~/.config/tidepool/config.toml` and stops, so you can look at it before anything is copied.

## Settings

The settings file has four keys. `library` is the folder the albums live in, `target` is the share to copy them to, such as `smb://shelf/photos`, `workers` is how many files are copied at once (4 by default; more rarely helps over Wi-Fi), and `skip` is a list of name patterns to leave out, such as `*.tmp` or `Thumbs.db`.

## Running

`tidepool sync` copies everything that is new or changed since the last run. Add `--dry-run` to see the list of changes without copying anything, and `--since 2026-01-01` to look only at albums changed after a date. `tidepool doctor` checks that the share is reachable and the settings file is valid, and prints what it found.

Set the environment variable `TIDEPOOL_LOG=debug` to get a detailed log in the terminal when something behaves oddly.

## Problems and what they mean

- TP-409, conflict: an album changed on the share since the last sync. Run `tidepool sync --resolve newest` to keep whichever copy is newer.
- TP-414: a file name is longer than the share accepts, 255 bytes. Rename the file and sync again.
- TP-503: the share is busy. tidepool waits 60 seconds and tries again, up to 5 times, before it gives up.
- TP-507: the share is full. Nothing is copied until there is room; old weekly copies are the first thing to clear.

## Deleting on purpose

`tidepool prune --older-than 2y` removes from the share the albums that were deleted from the laptop more than two years ago. It asks before removing anything and lists what it will remove first.
