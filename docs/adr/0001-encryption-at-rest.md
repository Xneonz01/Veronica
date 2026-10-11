# ADR 0001: Encrypting her data at rest

- **Status:** accepted, 2026-09-26, with the choices under "The owner's decision"
  below, and built: her memory (0.32.0), her voice clips (0.33.0) and the recovery key
  (0.34.0). Proposed the same day.
- **Changes the locked stack:** SQLite becomes SQLCipher (an encrypted build of SQLite),
  and two dependencies are added. That is why this is an ADR rather than a task.

## Context

Everything Veronica knows about a person sits in her data folder
([ARCHITECTURE §15](../../ARCHITECTURE.md#15-data-model)):

| What | Where | What it holds |
|---|---|---|
| Her memory | `memory/memory.db` (SQLite) | memories and the profile, every chat, the text of every document and attachment, projects, the activity log |
| Voices | `voices/*.wav`, `voices.json` | the reference clip of each cloned voice: a recording of a real person |
| Attachments | `attachments/` | files added in chat, as they were uploaded |
| Personality and policy | `persona.json`, `policy.json` | the personality text; which actions ask first |
| Diagnostics | `telemetry/`, `metrics/` | stage timings and events; no text, arguments or paths by design (ARCHITECTURE §14) |

Since 0.21 the folder is made private to the Windows account she runs as (ARCHITECTURE
§18), which
keeps other accounts on the same computer out. It does nothing about:

- a lost or stolen disk or laptop, or the disk read from another system;
- a copy of the folder: a backup, a cloud-synced folder, a folder copied to a USB stick;
- anyone who can read the files as another administrator.

ROADMAP step 2 (Hardening) lists encrypting the memory database and voice clips.

## What was measured

On the development machine (Windows 11, Python 3.12.14), with sqlcipher3-wheels 0.5.7,
cryptography 50.0.1 and sqlite-vec 0.1.9, in throwaway folders:

- sqlcipher3-wheels 0.5.7 ships SQLCipher 4.12.0 (community) on SQLite 3.51.1, with FTS5
  compiled in. sqlite-vec loads into it, and both the `vec0` vector search and FTS5 full-
  text search work inside the encrypted file.
- 2,000 rows written the way her memory writes them (a row, its full-text entry and a
  768-dimension vector): 456 ms plain, 475 to 479 ms encrypted (two runs each, the same
  SQLCipher build without a key as the plain one). Vector search (10 nearest of 2,000,
  median of 50) was no slower: 1.7 ms encrypted, 3.8 ms plain. The file is 2% larger
  (6,800 against 6,668 KB).
- Opening with a raw 32-byte key (no password stretching) takes about 0.2 ms.
- The encrypted file has no SQLite header. Opened with a wrong key or none, the first
  read fails with `DatabaseError: file is not a database`.
- aiosqlite, which the stores use, takes a connection factory
  (`aiosqlite.Connection(connector, iter_chunk_size)`), so a keyed SQLCipher connection
  needs no change to the library. Its `Row` type refuses a SQLCipher cursor
  (`TypeError`); SQLCipher's own `Row` works.
- SQLite's backup API copies an encrypted database to an encrypted copy (the copy kept
  before an upgrade is made that way): `quick_check` ok, every vector there.
- An existing plain database with `vec0` and FTS5 tables encrypts in place with
  `sqlcipher_export`: 500 rows in 52 ms, `quick_check` ok, every row and vector kept, both
  searches working.
- Windows DPAPI (`CryptProtectData`, current user, no prompts), called through `ctypes`
  from the standard library, wraps a 32-byte key into 262 bytes; wrapping and unwrapping
  take 0.17 ms. Unwrapping with different entropy fails.
- The voice model reads its reference clip with `librosa.load`, which reads a WAV from
  memory (`BytesIO`) as well as from a path, so a clip can be decrypted into memory and
  never written out.

Not measured yet: the full test suite on SQLCipher, a real `memory.db` migrated, and how
long a migration of a large file takes.

## Decision (proposed)

1. **Her memory in SQLCipher.** One keyed connector opens `memory.db` everywhere: the
   stores, the pre-upgrade copy and its check, the diagnostics. The standard `sqlite3`
   module no longer opens her files, so no path opens them without the key.
2. **Files with AES-256-GCM** (`cryptography`): voice clips, and attachments if they are
   in scope. Decrypted into memory when read, never to disk.
3. **One random 32-byte data key**, wrapped with DPAPI for the Windows account she runs
   as, in a small key file in the data folder with a versioned header. A fixed entropy
   string separates it from other DPAPI users; it can never change, or every key made
   with the old one is lost.
4. **A locked state, not a crash.** A key that can't be unwrapped (another account, a
   reset profile, a damaged key file) starts her without her memory, as a memory she
   can't open does since 0.25.0, with its own problem code and words on screen.
5. **A one-time migration** at the first start of the version that ships this: the
   plain `memory.db` is exported into an encrypted one, checked, and swapped in; the
   clips are encrypted beside it. The plain originals are removed once the encrypted
   copies check out.
6. **Diagnostics stay plain**: they hold no text, arguments or paths by design, and
   reading them must not need her key.

## What it costs

- Two dependencies: `sqlcipher3-wheels` (the binding is under the zlib licence; the
  SQLCipher library it bundles has a licence of its own, to confirm and add to
  `THIRD_PARTY_NOTICES.md`) and `cryptography` (Apache-2.0 or BSD-3-Clause). Both
  install from wheels on Python 3.12 for 64-bit Windows (`cp312-cp312-win_amd64` and
  `cp311-abi3-win_amd64`).
- About 4 to 5% on writes, 2% on file size, 0.2 ms per open.
- **Data loss if the key is lost.** DPAPI ties the key to one Windows account on one
  computer: a reinstalled Windows, a reset profile or the disk moved to a new computer
  can't unwrap it, and everything encrypted with it is gone. A recovery key (below)
  is the answer; without one, this trades the stolen-laptop risk for a lost-data risk.
- Effort: large. A key module, the connector and every place that opens the file, the
  migration, clip and attachment encryption, the locked state (backend and app), and
  tests for each, including the migration of a real-sized file.

## What it does not protect against

- Software running as the same Windows user while she runs: it can ask DPAPI for the
  key as she does, and her memory is decrypted in her process.
- A disk read by someone who knows, or can find, the account's password: Windows keeps
  the account's own DPAPI keys on that disk, protected by the password, so the key
  unwraps offline with it, and with nothing for an account that has none. BitLocker
  covers the disk itself. (Added after the review of 0.32.0, whose notes said a
  stolen disk couldn't read her memory, without this condition.)
- Anything in the process's memory. Python can't reliably wipe a key it has passed to
  SQLite as text.
- Text that has already left: a document's own file wherever it came from, or a copy of
  a chat pasted elsewhere.

## Options for the owner

1. **Scope.** (a) her memory only; (b) her memory and voice clips (recommended: the
   clips are recordings of real people); (c) (b) plus attachments; (d) (c) plus the
   personality text.
2. **Recovery.** (a) DPAPI only: simplest, and data is lost with the Windows account;
   (b) plus an optional recovery key, shown once and saved by the person, that can
   unwrap the data key on another computer (recommended); (c) a passphrase typed at
   every start: strongest, but she could no longer start on her own.
3. **Migration.** (a) automatic at the first start after the update, with a notice; (b)
   a switch in Settings, off until turned on.

## The owner's decision (2026-09-26)

- **Scope (b):** her memory and the voice clips.
- **Recovery (b):** DPAPI, plus an optional recovery key, shown once and saved by the
  person, that can unwrap the data key on another computer.
- **Migration (a):** automatic, at the first start of the version that ships it, with a
  notice.

## Consequences

- `CLAUDE.md`'s tech stack line changes from SQLite to SQLCipher.
- ARCHITECTURE §15, §18 (what is and isn't defended) and §21 record the decision, and
  this ADR moves to "accepted".
- The pre-upgrade copy (0.24.0) and every future copy are encrypted with the same key,
  so no plain copy of her memory is left beside the encrypted one.

## What the build found (0.32.0, her memory)

- **The key lives beside the file** (`memory.key` beside `memory.db`), not in the data
  folder as proposed. The tests pointed one memory file at several data folders, and a
  start in one encrypted it with a key the others couldn't find; a person moving the
  memory folder by hand, as the notices tell them to, or changing the data folder while
  the file keeps a place of its own, would have met the same lock. Beside the file, the
  two move together.
- **`sqlcipher_export` doesn't carry `user_version`**: 7 came out 0. The migration sets
  it by hand; without it every upgrade step would have run again on an upgraded file.
- **SQLCipher's errors are classes of their own**, not the standard library's, so every
  place that caught `sqlite3.Error` catches both (`cipher.SQLITE_ERRORS`). A wrong key
  reads as `SQLITE_NOTADB`, the same as a damaged file, and SQLCipher also reports it on
  stderr, which is turned off (`PRAGMA cipher_log_level = NONE`).
- **Encrypted is told from damaged by the first page's noise**: 7.95 bits a byte for
  SQLCipher's, 3.08 for a text file, 0.16 for a plain page whose header was wiped. A
  damaged file then reads as damaged, not as a memory whose key is missing.
- **Measured**: a 70.3 MB file (20,000 memories with 768-dimension vectors and 20,000
  chat messages) encrypted in 0.87 s, its check included; 1.6% larger. sqlcipher3-wheels
  0.5.7 statically links OpenSSL 3.6.0; its licences are in `THIRD_PARTY_NOTICES.md`.
- `cryptography` isn't needed yet: it comes with the voice clips.

## What the build found (0.33.0, her voice clips)

- **Sealed with AES-256-GCM** (`cryptography` 50.0.1, which links OpenSSL 4.0.2): a
  header, a random nonce, the ciphertext and its tag, 33 bytes more than the clip. The
  file's own name is authenticated with it, so a clip renamed into another voice's place
  doesn't open as that voice. The voices have a key of their own, `voices.key` in their
  folder, for the reason her memory's lives beside it: the folder moves whole.
- **Opened in memory only, and the model agrees**: on the real model (on the CPU), a
  sealed clip opened into a `BytesIO` prepares the same speaker embedding, prompt tokens
  and decoder embedding as the plain file.
- **Uploads were written out plain**: each clip went to a temporary file on its way into
  a voice, and a crash before its removal left a person's recording behind. They are
  read from memory now. Starlette still spools an upload over 1 MB to a temporary file,
  which Windows deletes when it is closed, crash or not.
- **A locked key doesn't stop a call**: a clip that can't be opened is spoken in the
  voice the model comes with (`conds.pt`, the same values `from_local` loads), and the
  app says why and what opens the voices again.
- **Measured**: ten 20 s clips sealed at the first start in about 100 ms; a later start
  opens their key in 2.7 ms; one clip opens into memory in 1.0 ms.

## What the review of 0.32.0 found

A review of her memory's encryption reproduced each of these in throwaway folders; all
are fixed in 0.33.0.

- **Two starts at once could lose the key**: one read the other's key file before it was
  written, set it aside as unusable and made its own, while the first encrypted her
  memory with the key set aside. The key is now opened, and her memory encrypted, under
  a lock beside it (`memory.lock`, the system's lock on its first byte), and her voices
  under theirs.
- **No DPAPI, no memory**: on a system other than Windows every start failed to open
  her memory. There is no key there now: she keeps her data plain and says so.
- **A rename cut in two could leave only the encrypted file** on FAT32 or exFAT, and the
  next start deleted it. It is now put in place when it reads back whole.
- **A key that doesn't fit its file read as a damaged file**, whose words point to copies
  the same key can't open. It is now `key_mismatch`, whose words give both ways out,
  since SQLCipher can't tell it from a file damaged in its first page.
- **One damaged copy from before an update** kept the copies after it plain and said her
  memory wasn't encrypted; a copy's encryption also restarted its 30 days. Each copy is
  now tried, a failing one named, and each keeps its time.
- **Smaller**: a leftover that couldn't be removed hid why a file wasn't encrypted; an
  empty wrapped key read as another account's; the unwrapped key was freed without
  being zeroed; the entropy was called a secret, though it is in the published source;
  and going back to a build before 0.32.0 is no longer possible, which the notes didn't
  say.

## What the build found (0.34.0, the recovery key)

- **It seals the data keys; it isn't one.** Each data key is sealed a second time with
  a key derived from the recovery key (HKDF-SHA256, AES-256-GCM through
  `core/sealed.py`) and kept in its own key file beside the DPAPI wrapping, so the key
  file carries both wherever it goes, as BitLocker keeps a volume's protectors with the
  volume. The recovery key itself is kept nowhere. It opens the key file, not the data
  alone: with `memory.key` lost it can't help, which the notices already cover by
  saying to move the key file with her memory.
- **160 bits, written for a person**: 36 characters in Crockford's base 32, the last
  four a SHA-256 check that caught every one of the 1,116 single-character changes of a
  key (measured), so a typo is said as a typo, not as the wrong key. Random bits need no
  slow password hashing.
- **All or none**: making one opens every data key before sealing any, so no key file
  keeps an older recovery key than another; a new one replaces the old. A key made
  since (a voices folder made again) isn't covered, and Settings says so.
- **Restored for this account, from the next start**: using it where Windows can't
  unwrap a key file wraps the key for this account; the start that follows opens it.
  Opening her data within the running process would mean starting the stores after the
  start, which nothing else does; the notices say to restart, as every other fix of a
  start's problem does.
- **What the browser found**: the new key was lost before it was shown (the row that
  made it gave way to the one for a key made, and took the key with it), and with both
  notices offering the key, the second's button sent the first's empty form. Both fixed
  before release, each with a test.

## What the review of the recovery key found (0.34.0, before release)

- **A second Escape closed the key's sheet unsaved** (High): Chromium won't let a page
  refuse it, the sheet's owner still said open, and every later key was sealed without
  being shown. The shared sheet now shows itself again while its owner keeps it open.
- **A key could be made while her memory was locked** by a key that unwraps but opens
  nothing, or with its key file missing, and Settings then said it covered everything.
  The start's lock notices now count as locked.
- **Another program could make a key unnoticed**, recorded as the person's. The log
  now says who asked, every window warns of a key a program made, and Settings shows
  the time.
- **Making one wasn't all or none**: a key file held open by a reader, or two makes at
  once, left files sealed by different keys while the status, judging by a timestamp to
  the second, said one key covered both. Now every lock is held at once, the files are
  written together or put back, and a key id replaces the timestamp.
- **The check could miss a typo**: a 20-bit hash let a changed character through in
  about 1 key of 1,000, and its test failed about 1 run in 1,000. A CRC-20 catches every
  changed character and neighbour swap, for every key.
- Smaller: a sealed file cut short failed with the cipher's own error; the refusals
  showed the server's words; two notices each offered the key; the key stayed on the
  clipboard; the keyboard landed on a Close that did nothing.
