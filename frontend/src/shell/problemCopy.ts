// The words for what keeps her from working fully, by the code /health reports. The
// backend sends no words of its own for these, only the values they need (a file
// name, two sizes, model codes), so nothing here is server text. The app doesn't start
// or stop her backend, so the steps name it: reopening the app alone changes nothing.

import type { NoticeTone } from '../ui/Feedback'
import type { Problem } from './useProblems'

/** `tone` is critical unless said: attention when she carries on without it, less well. */
export type ProblemCopy = { title: string; body: string; tone?: NoticeTone }

const UNTIL = 'Chats, memories and documents are unavailable until then.'
const MEGABYTES = new Intl.NumberFormat('en-US')

// SQLite's own files beside it (memory.db-journal) go too: one a crash left would be
// applied to whatever file is named memory.db next, and damage the copy.
const goBack = (copy: string) =>
  "stop Veronica's backend, move memory.db and any file beside it whose name starts with " +
  `memory.db- somewhere safe, rename ${copy} to memory.db, then start the backend again.`

export function problemCopy({ code, detail }: Problem): ProblemCopy {
  switch (code) {
    case 'memory_too_new':
      return tooNew(copyName(detail.copy))
    case 'memory_damaged':
      return {
        title: 'Her memory file is damaged',
        body:
          "She can't read it, so chats, memories and documents are unavailable. If a copy from " +
          'before an update sits beside it, named like memory.db.v5.bak, you can go back to it: ' +
          goBack('the copy'),
      }
    case 'memory_unopenable':
      return {
        title: "Veronica couldn't open her memory",
        body: `Another copy of Veronica may be using it, or its folder may be closed to her. Close any other copy, then restart Veronica's backend. ${UNTIL}`,
      }
    case 'memory_no_room':
      return noRoom(megabytes(detail.needed_mb), megabytes(detail.free_mb))
    case 'memory_copy_failed':
      return {
        title: "Veronica couldn't back up her memory before updating it",
        body: `So she left it as it was. Restart Veronica's backend to try again. ${UNTIL}`,
      }
    case 'memory_locked':
      return locked(detail.reason, detail.recoverable === 1)
    case 'memory_not_encrypted':
      if (detail.reason === 'unsupported') return notEncryptedHere
      return notEncrypted(megabytes(detail.needed_mb), megabytes(detail.free_mb), detail.copy)
    case 'memory_left_behind':
      // Whole folders: memory's holds SQLite's own files and the copies kept before updates.
      return {
        title: "Her memory is still in Veronica's own folder",
        body:
          "Her data folder was moved, but her memory and voices are still in the data folder inside Veronica's " +
          "folder, so she didn't start a new memory without them. Stop Veronica's backend, move the memory and " +
          `voices folders from there into her data folder, then start the backend again. ${UNTIL}`,
      }
    case 'models_missing':
      return missingModels(detail.models)
    case 'voices_damaged':
      // The clips themselves are untouched: only the list naming them couldn't be read.
      // A voice made now is gone again at the next start, as the default is: the
      // seeding script brings the default back for good.
      return {
        title: "Her voices file couldn't be read",
        body:
          'She set it aside in her voices folder, named voices.json.damaged with the date, and started ' +
          'without her saved voices; their clips are still there. To make calls again, run ' +
          `${SEED_VOICE} in her backend folder, then restart Veronica's backend. Your own voices can be made ` +
          'again in the Voice Studio.',
      }
    case 'voices_unusable':
      // Her voices file, or the folder and its key file: the steps are the same.
      return {
        title: "Her voices can't be used",
        body:
          "Her voices file or folder couldn't be read or written, so she started without her voices and can't " +
          "save a new one. Close any program that has her voices folder open, and check that the folder is one " +
          "she can write to, then restart Veronica's backend.",
      }
    case 'voices_locked':
      return voicesLocked(detail.reason, detail.recoverable === 1)
    case 'data_recovered':
      return recovered(detail.memory === 1, detail.voices === 1)
    case 'audit_broken':
      return auditBroken(detail.row)
    default:
      // A code from a newer backend than this app: still said, never as its code.
      return { title: "Part of Veronica didn't start", body: "Restart Veronica's backend to try again." }
  }
}

// She carries on as before, so it is said calmly; the banner offers the review.
function auditBroken(row: string | number | undefined): ProblemCopy {
  const where = typeof row === 'number' && row > 0 ? `, starting at entry ${row.toLocaleString('en-US')}` : ''
  return {
    title: 'Her activity log was changed outside Veronica',
    body:
      `Something other than Veronica changed or removed entries in the record of what she has done${where}. ` +
      'If you made the change, or it happened under an earlier version of Veronica, you can mark it as reviewed.',
    tone: 'attention',
  }
}

function tooNew(copy: string | null): ProblemCopy {
  const title = 'Her memory is from a newer version of Veronica'
  const update = 'Nothing in it was changed, but chats, memories and documents are unavailable until you update Veronica.'
  if (copy === null) return { title, body: update }
  return {
    title,
    body: `${update} To go back instead, ${goBack(copy)} Anything saved since that copy stays in the file you moved.`,
  }
}

// Her memory file is encrypted with a key kept beside it, memory.key, which only the
// Windows account she was set up under can open (ADR 0001). A recovery key, if one was
// made, opens it here too (`recoverable`): the notice then offers it.
function locked(reason: string | number | undefined, recoverable: boolean): ProblemCopy {
  if (reason === 'key_missing') {
    return {
      title: "Her memory's key is missing",
      body:
        "memory.key, which opens her memory file, isn't beside it. If you moved her memory folder, move " +
        `memory.key into it too, then restart Veronica's backend. ${UNTIL}`,
    }
  }
  if (reason === 'key_damaged') {
    return {
      title: "Her memory's key file is damaged",
      body:
        "memory.key, beside her memory file, can't be read. If you have a copy of it from before, put it back, " +
        `then restart Veronica's backend.${recoverable ? OR_RECOVERY : ''} ${UNTIL}`,
    }
  }
  // SQLCipher can't tell a key that isn't this file's from a file damaged where the key
  // is checked, so both ways out are given.
  if (reason === 'key_mismatch') {
    return {
      title: "Her memory doesn't open with its key",
      body:
        "memory.key, beside her memory file, doesn't open it: it may be another memory's key, or the file may " +
        'be damaged. If you moved or replaced memory.key, put the right one back (one she set aside is named ' +
        "memory.key.unusable with a date), then restart Veronica's backend. Otherwise, if a copy from before " +
        `an update sits beside it, named like memory.db.v5.bak, you can go back to it: ${goBack('the copy')}`,
    }
  }
  return {
    title: 'Her memory is locked to another Windows account',
    body:
      "It is encrypted for the Windows account she was set up under, and this account can't open it. Sign in " +
      `to that account and start her there.${recoverable ? OR_RECOVERY : ''} ${UNTIL}`,
  }
}

// She carries on with it as it was, and tries again at the next start. A copy kept from
// before an update that can't be encrypted may be damaged; it goes after 30 days.
function notEncrypted(needed: string | null, free: string | null, copy: string | number | undefined): ProblemCopy {
  const room =
    needed !== null && free !== null
      ? `Encrypting it needs ${needed} MB free beside it, and ${free} MB is free. Free up some space, then restart Veronica's backend.`
      : null
  if (copy !== undefined) {
    const name = copyName(copy) ?? 'one of them'
    return {
      title: "A copy of her memory isn't encrypted yet",
      body:
        `She keeps a copy of her memory from before each update for 30 days, and ${name} couldn't be ` +
        'encrypted at this start, so it stays readable on this disk. ' +
        (room ?? "It may be damaged. She'll try again at the next start, and removes it once it is 30 days old."),
      tone: 'attention',
    }
  }
  const why = room ?? "Another program may have it open, or the disk may be full. She'll try again at the next start."
  return {
    title: "Her memory isn't encrypted yet",
    body: `She couldn't encrypt her memory file at this start, so it stays as it was, readable on this disk. ${why}`,
    tone: 'attention',
  }
}

// The key is kept with Windows' own protection (DPAPI), so there is none elsewhere.
const notEncryptedHere: ProblemCopy = {
  title: "Her memory isn't encrypted on this system",
  body:
    "Encryption at rest keeps its key with Windows, and this system isn't Windows, so her memory and voice " +
    'clips are stored unencrypted, readable on this disk. Keep her data folder on an encrypted disk to protect them.',
  tone: 'attention',
}

// Her voice clips are sealed with voices.key, in her voices folder (ADR 0001). She still
// speaks, in the voice her speech model comes with.
const VOICES_UNTIL = "Until then she speaks in her built-in voice, and no voice can be chosen, played or made."

function voicesLocked(reason: string | number | undefined, recoverable: boolean): ProblemCopy {
  if (reason === 'key_missing') {
    return {
      title: "Her voices' key is missing",
      body:
        "voices.key, which opens her voice clips, isn't in her voices folder. If you moved that folder, move " +
        `voices.key into it too, then restart Veronica's backend. ${VOICES_UNTIL}`,
      tone: 'attention',
    }
  }
  if (reason === 'key_damaged') {
    return {
      title: "Her voices' key file is damaged",
      body:
        "voices.key, in her voices folder, can't be read. If you have a copy of it from before, put it back, " +
        `then restart Veronica's backend.${recoverable ? OR_RECOVERY : ''} ${VOICES_UNTIL}`,
      tone: 'attention',
    }
  }
  return {
    title: 'Her voices are locked to another Windows account',
    body:
      "Her voice clips are encrypted for the Windows account she was set up under, and this account can't " +
      `open them. Sign in to that account and start her there.${recoverable ? OR_RECOVERY : ''} ${VOICES_UNTIL}`,
    tone: 'attention',
  }
}

const OR_RECOVERY = ' Or use the recovery key you saved.'

// What a recovery key opened for this account, which the next start uses.
function recovered(memory: boolean, voices: boolean): ProblemCopy {
  const what = memory && voices ? 'Her memory and voices open' : memory ? 'Her memory opens' : voices ? 'Her voices open' : 'Her data opens'
  const them = voices ? 'them' : 'it'
  return {
    title: `${what} on this computer now`,
    body: `Your recovery key opened ${them} for this Windows account. Restart Veronica's backend to use ${them}.`,
    tone: 'attention',
  }
}

function noRoom(needed: string | null, free: string | null): ProblemCopy {
  if (needed === null || free === null) {
    return {
      title: 'The disk is full',
      body: `Veronica couldn't write to her memory. Free up some space, then restart Veronica's backend. ${UNTIL}`,
    }
  }
  return {
    title: "There isn't room to update her memory safely",
    body:
      `She keeps a copy of it before an update, which needs ${needed} MB, and ${free} MB is free. ` +
      `Free up some space, then restart Veronica's backend; nothing was changed. ${UNTIL}`,
  }
}

// What each model is for, by the code the backend sends (`adapters/model_files.py`,
// which a backend test holds this list to). A Map, so no code can reach an object's own
// properties (`constructor`).
const MODELS = new Map([
  ['stt', 'speech recognition'],
  ['tts', 'speech synthesis'],
  ['turn', 'end-of-turn detection'],
  ['embedding', 'her memory model'],
  ['rerank', 'document ranking'],
])
export const INSTALL = 'uv run python -m scripts.install_models'
const SEED_VOICE = 'uv run python -m scripts.seed_default_voice'

// What goes without them. A call's start loads the memory model and both voice models;
// chat answers without the memory model, but without her memory or the documents too
// (each answer says so; it failed outright before 0.34.1). End-of-turn detection and
// document ranking are done without, less well (a longer wait, plainer ranking).
function missingModels(codes: string | number | undefined): ProblemCopy {
  const missing = new Set(String(codes ?? '').split(',').filter(Boolean))
  const named = [...missing].flatMap((code) => MODELS.get(code) ?? [])
  const known = named.length === missing.size
  const memoryDown = missing.has('embedding')
  const callsDown = memoryDown || missing.has('stt') || missing.has('tts')
  const effects = [
    memoryDown ? 'Chat answers without her memory or your documents.' : '',
    callsDown ? "Calls can't start." : '',
    !callsDown && missing.has('turn') ? 'Calls wait a little longer before she answers.' : '',
    !memoryDown && missing.has('rerank') ? 'Documents are searched a little less precisely.' : '',
  ].filter(Boolean)
  const one = missing.size === 1
  const which = named.length > 0 ? `Not installed: ${named.join(', ')}. ` : ''
  return {
    title: one ? "One of her models isn't installed" : "Some of her models aren't installed",
    body: `${which}${effects.map((effect) => `${effect} `).join('')}Install ${one ? 'it' : 'them'} with ${INSTALL} in her backend folder, then restart Veronica's backend.`,
    // A code this app has no words for is taken as one she can't do without.
    tone: callsDown || !known ? 'critical' : 'attention',
  }
}

/** A copy's file name as the backend makes them (`memory.db.v5.bak`); nothing else is shown. */
function copyName(value: string | number | undefined): string | null {
  return typeof value === 'string' && /^[\w-][\w .-]{0,100}\.v\d{1,6}\.bak$/.test(value) ? value : null
}

function megabytes(value: string | number | undefined): string | null {
  return typeof value === 'number' && value >= 0 ? MEGABYTES.format(value) : null
}
