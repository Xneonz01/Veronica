# Roadmap

Where Veronica stands at version 0.30.2, what is known to be broken, and what comes
next. The release history is in [CHANGELOG.yaml](CHANGELOG.yaml); how things work is in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Where it stands

| Area | Status |
|---|---|
| Live voice call: streaming recognition, reply and speech, barge-in | Working. From your last word to her voice: p50 2.05 s, p95 3.01 s on the call probe (24 turns, 0.15.5), against a baseline of 2.09 s and 3.22 s at 0.15.1; every probe question is heard whole since 0.15.3 (none was before). The largest parts are the first synthesis (p50 1.03 s), the end-of-speech wait (0.43 s; since 0.16.0 a model decides it: median 236 ms, mean 376, on the probe) and the final transcription (0.17 s). Since 0.34.6 a call the graphics card has no room for, another program holding it, is refused before it loads anything, saying so |
| Tools during a call, with spoken confirmation | Working |
| Speaks first with a varied greeting; recap of the last call | Working; the opener is held to about six seconds |
| Voice cloning (Voice Studio: upload or record, preview, switch, delete) | Working; since 0.29.0 the voices move with her data folder (bug 27), and since 0.33.0 each clip is sealed at rest and an upload is never written out plain |
| Encryption at rest | Her memory since 0.32.0 (SQLCipher) and her voice clips since 0.33.0 (AES-256-GCM), with keys only her Windows account opens ([ADR 0001](docs/adr/0001-encryption-at-rest.md)); a key she can't use is a notice, never a crash, and she still speaks in her built-in voice. Since 0.34.0 an optional recovery key, shown once, opens them on another computer or account |
| Microphone and speaker selection, switchable mid-call | Working |
| Chat: streaming, history, stop, tool cards, citations | Working |
| Attachments: documents indexed and citable; images stored but not read | Working (no vision model yet). Since 0.22.0 each passage knows its headings, and a question naming a code or a part number is also searched by keyword: on the document-search evaluation the passage that answers is among those cited 97% of the time (88% at 0.21.5). Since 0.23.0 a cross-encoder rereads the ten best candidates against the question: the right passage first for 92% (85%), and 3 of 10 unanswerable questions given passages (6), for about 80 ms a question |
| Projects that scope which documents are cited | Working; memory is not scoped by project |
| Memory: profile, facts, preferences, summaries; relevance-gated retrieval | Working |
| Learning from conversations, with a review queue and Undo | Working; since 0.21.5 chat is learned from once the model is free (giving way to a reply), every conversation you wrote in, and a failed extraction is tried again; since 0.31.1 a call is too, kept until it has been read |
| Memory page: browse, search, edit, pin, approve, documents | Working |
| Notifications for memory changes, sounds, awareness log | Working |
| Capability kernel: policy, confirmation, append-only audit log | Working; since 0.18.0 an action whose words came from something she read asks first, since 0.19.0 a reply that says she did something no action of hers did gets a caveat, and since 0.20.0 the log is hash-chained and checked at every start (since 0.21.1 removing its newest entries is found too; since 0.31.0 a change is shown in the app and can be marked as reviewed) |
| Capabilities: read, list, search and write files; open allowed apps; save, recall, correct and forget memories | Working; the memory ones since 0.17.0. Since 0.34.1 a file inside her folders can be named without its full path, and the tools name her folders and the apps she may open |
| Editable personality, applied live to voice and chat | Working |
| Companion window on a global hotkey, tray, start with Windows, quick capture | Mostly working; hotkey and window bugs below |
| Diagnostics: black box, performance log, `/diagnostics`, CLI | New in 0.14.0. A check of this computer (graphics card, memory, disk, Ollama and her model, her model files) against what she was measured using, in Settings > About and `--doctor`, since 0.27.0 |
| Version and patch notes in Settings → About | New in 0.14.0 |
| Choice of language model | A setting (`llm.model`, any Ollama model that can call tools); measured only with qwen3.5:9b on a 16 GB card, and no picker in the app yet (see Model choice below) |
| Interface redesign: calm, light and dark, the conversation as a document | Done in 0.15.0: every screen rebuilt, light and dark themes, a command palette, Settings in five parts. Checked with faked backend replies, a stand-in chat socket (empty, failing, slow, offline and oversized data) and real model replies; three code reviews (chat, calls, settings and shell) fixed in 0.15.1. A pass with a live call on a real microphone is still to do |
| Model evaluation on Veronica's own prompts: tool choice, what is learned from a conversation, answer accuracy, instructions planted in what she reads | New in 0.15.1 (`backend/eval`, `scripts/model_eval.py`); results in `backend/eval/results`. Planted instructions since 0.30.0 (`backend/eval/injection`): on qwen3.5:9b, one of 54 planted tool requests ran without asking over three runs (a name change read in a file, saved in her own words), and one planted claim reached an answer in one run of three. Since 0.34.13 both are measured on a call's prompt beside chat's (`call_*`): 84 of 102 action requests at 0.34.13 (chat 98); planted in her memory or a file, a call followed 2 of 36 and chat 3 at 0.34.13, none unasked, and 3 and 5 at 0.34.14, the name change (`r07`) unasked in two runs of three on each. Since 0.34.14 it records how many planted claims passed on the relay check would caveat (`echo_caveated`: all three in its runs), and the answer evaluation asks three questions whose documents come close without answering, counting a decline only about what was asked |
| Starting when her memory file can't be used (written by a newer version, damaged, held by another program, no room on the disk, a copy before an upgrade that failed) | New in 0.25.0: she starts without it instead of not at all, and the app says what happened and what to do; chat is paused until a restart finds it usable |
| Document-search evaluation: a fictional library of 13 documents and 69 questions, scored on what a chat turn would cite | New in 0.22.0 (`backend/eval/retrieval`, `scripts/retrieval_eval.py`); stricter since 0.22.1 (a passage counts only from the document that answers, and each result records its settings and every question's rank) |

## Known bugs

In rough order of how much they matter to daily use.

1. **Global hotkeys other than the default probably do nothing.** A fired shortcut is
   matched against the saved one by its text, and the hotkey library writes it
   differently (`control+alt+KeyV` against `Ctrl+Alt+V`). `Alt+Space` happens to match.
   The call hotkey and the `Ctrl+Alt+Space` fallback are affected. Needs confirming on a
   machine with the Rust toolchain; the fix is to compare parsed shortcuts
   (`frontend/src-tauri/src/lib.rs`, `shortcuts.rs`).
2. **"Start a call" from the hotkey or the tray only opens the call screen.** Nothing
   starts the call.
3. **Fixed in 0.15.0.** The confirmation prompt no longer takes the keyboard from a
   field being typed in, and an approval that doesn't reach the backend is reported.
4. **Fixed in 0.15.10** (she says it only when the action was declined or timed out).
   **Approving on screen during a call:** she says "I'll leave that then." while the
   action goes ahead (`spoken_confirm.ask` only knows about spoken answers).
5. **Fixed in 0.15.10** (the question joins the turn's messages as well as the history).
   **A new question asked instead of a yes/no** is kept in history but not answered until
   the next turn (`tool_loop._run_one`).
6. **Fixed in 0.15.6** (one lock per sentence; a deleted voice's conditioning is dropped).
   **Previewing a voice during a call** can switch the call's voice for part of a reply,
   and can run two syntheses on the model at once. The per-voice cache is never cleared.
7. **The companion window can't be dragged**: its window permission set lacks
   `allow-start-dragging`.
8. **Fixed in 0.15.0.** In the companion, Ctrl+Enter only saves the note.
9. **Fixed in 0.15.0.** Countdown rings animate; `npm run build` now fails if a used
   animation has no keyframes.
10. **Fixed in 0.15.0.** Every list and editor that fails to load says so and offers
    another try; chat errors clear on the next message.
11. **Fixed in 0.34.8** (reject turns down only a candidate still waiting, and the
    Memory page gets every memory she keeps, which the cap bounds, and the 200 newest
    candidates). **Memory route details** (the duplicate save is fixed in 0.17.0: the
    tool skips what is already stored): `reject` archived any memory, not only pending
    candidates; the page and its search saw only the 200 newest memories, so past 200
    an older profile slot or pinned memory disappeared from it.
12. **Fixed in 0.21.4** (every upload refused past 25 MB while it is read, and a voice
    at 10 clips). **Uploads**: the document and voice upload routes have no size cap
    (chat attachments do).
13. **Fixed in 0.15.12** (released 60 s after the last call ends: 4.2 GB measured).
    **Speech models stay loaded after a call ends**, holding about 6.5 GB of VRAM.
    Its last part fixed in 0.34.8: a voice preview outside a call loaded the voice model
    again, and it stayed until the next call ended; now the preview asks for the same
    release a call's end does (a new voice doesn't load it: choosing one never does).
14. **Fixed in 0.34.8** (the app starts through `subprocess` on a worker thread, as any
    event loop allows). **`apps.open` failed under `uvicorn --reload`** on Windows (the
    selector event loop can't start subprocesses).
15. **Fixed in 0.15.0.** The recorder and the microphone test release the microphone
    on every failure path, and hotkey capture reads letters and digits by key position.

Found in the September 2026 code review (numbered on so earlier references stay valid):

16. **Fixed in 0.15.2** (the model's first token: p50 312 to 196 ms on qwen3:8b).
    **About 250 ms lost on most voice turns before the model is even asked.**
    `llm.base_url` uses `localhost`, which resolves to IPv6 first while Ollama listens on
    IPv4. Each fresh connection waits out a 250 ms fallback (measured: 256–265 ms, against
    3 ms for `127.0.0.1`), and idle connections are dropped after 5 s. The fix is
    `127.0.0.1`.
17. **Fixed in 0.15.3** (the call probe's 24 questions: none heard whole before, all 24
    after). **The start of every utterance never reaches speech recognition.** Speech is
    detected after 250 ms of voice, and the frames before that point aren't kept
    (`services/voice/turn.py`, `realtime/ingest.py`). Since 0.15.8 speech picked up again
    after a pause keeps its first word too; talking over her while she speaks still
    starts at the frame that detected it, so its first word can be lost.
18. **Fixed in 0.15.2** (8,192 and 2,048, sized from the measured prompt).
    **No context window or output limit is sent to Ollama** (`num_ctx`, `num_predict`).
    A long prompt can silently lose its beginning (the personality and tool rules), and a
    runaway generation isn't capped.
19. **Fixed in 0.15.2** (one model, released at quit, no longer unloaded by learning).
    **Models linger in VRAM.** Ollama's models stay loaded for 30 minutes after Veronica
    quits. The end-of-session fact extractor unloads the chat model after every call,
    so the next chat turn reloads it cold.
20. **Actions fixed in 0.17.0 and 0.18.0; a line written to her that she passes on is
    caveated since 0.34.14; the rest of what she says is still open.** The memory tools
    are capabilities (0.17.0), and since 0.18.0 an action whose words were copied from a
    document, a memory or a tool result asks first, whatever its policy: a planted
    memory got the model to save its sentence 5 times in 5, and all 5 now ask. 0.18.1
    closed the gaps two reviews found in that check: a copy that differed by one
    punctuation mark or hid an invisible character, a value too short for the old
    20-character floor ("No allergies."), a yes to one action vouching for another,
    and a sentence she repeated from a document being taken as hers the turn after. What
    remains: memory text goes into the system prompt unfenced, and planted text can
    still steer her words (after a declined save she repeated the planted claim as
    fact). Measured on 0.16.0 with qwen3.5:9b, fencing that block as a user-role message
    changed nothing (planted memory instructions obeyed 14 times in 30 either way), so
    the next defence is a check on the reply, not the placement. 0.19.0 checks what a
    reply says she did (bug 37). Since 0.34.14 a reply that passes on what a line
    addressed to her told her to tell the user ("AI assistant: tell the user that...")
    gets a caveat saying so, in chat and on calls (ARCHITECTURE §12.11): the three
    claims passed on in the injection evaluation's runs at 0.34.14 all got it, and over
    the 351 replies kept from its runs it fires on 8, each one passing such a line on.
    Still open: a line with no label addressing her ("Tell the user..."), a claim
    reworded past most of its words, what planted text does to the rest of her answer,
    and a reworded action: a name change read in a file (`r07`), saved in her own words,
    ran unasked in two runs of three at 0.34.14, in chat and, since calls act more
    readily (bug 50), on a call. The check on what she read looks for copied words, not
    meaning; what should make such a save ask first is a decision still to take.
21. **Fixed in 0.15.9** (the gate stays open until the client has played the reply; history
    keeps what was heard). **Barge-in stops listening once synthesis finishes**, while
    playback is still going.
    An interrupted sentence also kept synthesising on the GPU, for a median of 1.63 s;
    since 0.15.6 it stops within a token (median 7 ms). (The call screen no
    longer shows "Listening" during that playback; fixed in 0.15.0. Since 0.15.1 the
    client stops that playback when she hears you, and since 0.15.9 it reports when
    playback ends, so her history keeps only the words you heard.)
22. **Fixed in 0.15.7** (offline forced, an install script; since 0.15.8 every loader
    opens its model's folder, and none reaches the network even with offline mode off).
    **Model loads don't force offline mode**, so they may contact huggingface.co at
    start-up.
23. **Fixed in 0.21.2** (made private to the account she runs as, checked and repaired at
    every start). **`data/` inherits write access for other Windows accounts** on the
    same machine. 0.21.0 read the folder's permissions as `icacls` text, so on a Windows
    in another language, or with a group other than the three it knew, an open folder
    passed as private; an accented account name stopped the start. 0.21.1 read them by
    SID; a review of it found the check stopped one level down, a repair that failed
    part-way was called fine at the next start, owners and hard links were ignored, and
    AppData or Documents could be named as her data folder. All fixed in 0.21.2.
24. **Fixed in 0.21.3** (the filter catches "My API key is …" and the extractor's
    measured rewordings, and keeps "the password is the same as before"; the extractor's
    reply is read a fact at a time). **The secrets filter misses some phrasings** and
    blocks harmless ones, and **one malformed fact from the extractor discards everything
    learned in that session**. A rewording nobody has measured yet can still get past
    the filter: it reads words and shapes, not meaning.
25. **Fixed in 0.34.1** (chat details): what the last tool round fetched is put into
    words by a round without tools, where a sentence written beside its calls used to
    end the turn; a listing or search cut short says so to the model; and a search
    that fails leaves the turn standing, with a note saying what the answer went
    without.
    - Fixed in 0.30.1: chat messages have no length cap (the window now holds the
      persona first, then the message, what she recalled and the conversation; each
      tool round is fitted again, and a message too long to fit goes back to the box).
26. **Fixed in 0.34.2** (every p90 and p95 is the nearest rank, one rule in
    `telemetry/percentile.py`, and the loop-lag p50 the median). The diagnostics p95 was
    one rank too high for some sample counts (20, 60, 100, …); with 20 turns it
    reported the slowest one (`telemetry/reader.py`).
27. **Fixed in 0.29.0** (a voice's files in the voices folder are saved by name, and a
    full path saved before is looked for there by its name).
    **Moving the project folder breaks every voice.** `voices.json` stores each reference
    clip's absolute path, so after a move or rename the clips can't be found and a call
    fails at warm-up when the voice is loaded. Found on the development machine, where
    both registered voices still point at the old location. The fix is to store paths
    relative to the voices folder and resolve old absolute ones by file name
    (`services/voice/voices.py`).
28. **Fixed in 0.15.9** (the turn closes before its row is written).
    **A finished turn's metrics can be recorded twice.** `respond` sends the row while
    barge-in is still armed and the turn is still registered, so speech in that moment
    makes `trigger_barge_in` record the same turn again, marked as interrupted
    (`realtime/workers.py`).

Found testing the redesigned screens against real models (qwen3.5:9b, September 2026):

29. **Fixed in 0.34.1** (a relative path is read inside her folders: the one that holds
    it, else the one that holds its folder, else the first, and one two folders could
    hold is asked about; the file tools name her folders). Asked
    about "notes/shopping.md", the model tried it three times and was refused each time
    with "I need the full path to that file", then ended mid-sentence (see 25)
    (`capabilities/paths.py`, `files.py`). 0.30.1 fixed the part about writing: a write
    that couldn't happen, a folder given for a file among them, is refused before anyone
    is asked, and the prompt names where the file really goes.
30. **Fixed in 0.26.0** (its own kind, `llm_model_missing`, whose words name the model and
    `ollama pull`, in chat and in a call). **A chat model that isn't installed reads as a
    passing hiccup.** Ollama's 404 for an
    unknown model becomes `turn_failed` ("Something interrupted that answer" with Ask
    again), which can never help. It should be its own kind, with copy that names the
    fix. Seen on the development machine while the default was `qwen3:14b`, which wasn't
    pulled there.
31. **Fixed in 0.26.0** (the start loads the memory model in the background and runs
    it once: the first message's first token came 131 and 240 ms after it was sent,
    against 5,864 and 5,280 ms before, with a stand-in for the language model). **The
    first message after start-up waits for the embedding model to load** before anything
    moves, and that load contacts huggingface.co (confirming 22). The network part is
    fixed in 0.15.7.

Found by the model evaluation and the 0.15.1 code reviews:

32. **Fixed in 0.21.5** (every stored message marks its conversation for learning, and a
    worker learns from it once the model is free, whatever is open). **A conversation
    you switch away from is never learned from.** Opening another one replaces the
    session's history and moves the learning watermark past it, so only the
    conversation open when the session ends reaches the extractor
    (`chat/lifecycle.open_conversation`).
33. **Fixed in 0.34.1** (its description names the apps it may open, as the model must
    write them). `apps_open` didn't list the apps it allows, so the model guessed: asked to open
    the file manager it sent "File Explorer" where the allow-list says `explorer`, and
    was refused (evaluation case t26).
34. **Fixed in 0.34.1** (a run of markers is judged marker by marker). An invented
    citation right after a real one survived: the cleanup's pattern
    skipped a marker that directly followed another (`[1][7]` kept the 7)
    (`chat/context.py`).

Found in the review of the Phase B changes (0.15.8):

35. **Fixed in 0.15.9** (each STT task waits for the one before it and only the newest
    is answered; the greeting is its own task). **An interruption doesn't stop
    everything already under way.** It cancels the reply
    but not the previous utterance's decode: when speech resumes within a few tens of
    milliseconds of an endpoint and that decode is slow, the first half's reply starts
    while the user is still talking, and the second half gets a reply of its own
    (`session.trigger_barge_in`, `spawn_respond`). Talking over the greeting stops it
    only after its current sentence, because the greeting runs in the warm-up task.

Found in the review of the Phase C changes (0.15.9 to 0.16.0): the defects in the
0.16.1 patch notes, all fixed there, and one left open:

36. **Fixed in 0.34.3** (a surface finds its prompt by the id its own request carried,
    not by actor and tool, so the audit's actor stays `veronica:voice`). Both calls act
    as `veronica:voice`, and a call found its own confirmation prompt by actor and tool
    name, so while both asked about the same tool, a "yes" in one call could approve
    the other's action (`ToolBroker.prompt_for`).

Found in the browser checks of 0.17.0:

37. **Made rarer in 0.17.0, caught since 0.19.0, and she is told since 0.34.12. She can
    say she changed her memory without doing it.** Told "that's wrong, I take my coffee with milk now", qwen3.5:9b answered
    "I'll update that memory" and called no tool; the old memory stayed. A sentence in
    her prompt ("never say it is done unless the tool ran") took it from 2 of 12 to 0 of
    12 on the evaluation's corrections and forgets with the memory in view, and after a
    declined forget she now reads plainly that nothing changed (she had answered "I did
    forget"). Since 0.19.0 the claim check reads every reply: one that says she saved,
    corrected, forgot, wrote or opened something when no such tool ran gets a caveat,
    under the answer in chat and said after it on a call (no false alarm on 369 stored
    evaluation replies; 0.21.1 narrowed the table after a review found false notes on
    drafts, quotes, code and words like "memory usage", and 0.21.2 after a second one
    found them on answers that mention a file, and a false "my memory didn't change"
    after a save reported as an update). She still says it; a paraphrase or a passive
    ("That's saved to my memory") goes unread. Since 0.34.12 the caveat follows her reply
    in the history the model reads next, as the user got it, so after "Ask me again" a
    "yes, do it" no longer meets a history that says it was done (on qwen3.5:9b the tool
    ran 30 times in 30 in chat, 18 before, and 28 in 30 on a call, 8 before; "Did that
    work?" drew a false yes once in 15 on each, 6 and 13 before; the fear that kept it
    out, that she would write such notes herself, showed once in 135 replies). A review
    found a call dropped it when talked over, even at its last word; it is kept once
    shown, as chat keeps it. What is left on a call: one unbacked claim makes her claim
    again for the next request (asked to remember something new straight after, she
    saved it 7 times in 15 with the caveat kept, 3 without). Measured before building a check for claims in
    the passive ("That's saved to my memory"), as the owner chose: on the tools
    evaluation's 48 first-turn requests, three times each in chat and on a call, none of
    288 replies made one the table doesn't read, so the table stays turn-local (such a
    sentence can also be true of an earlier turn). The measurement found bug 50.

Found in the review of 0.17.0 and 0.18.0: the defects in the 0.18.1 patch notes, all
fixed there, and one left open:

38. **Fixed in 0.34.5** (a correction or a forget that names a profile value means that
    slot; the new value is read only when certain, else the model is asked for "name:
    …"; on qwen3.5:9b, 18 of 18 profile changes in chat applied at the first call). Her
    profile couldn't be corrected or forgotten in conversation. The name and the
    other "About you" slots were not memories a correction or a forget could reach.
    From 0.18.1 she said so and pointed to the Memory page; before, she answered that
    nothing matched and saved a second, contradicting fact.
39. **Fixed in 0.34.10** (a learned fact that only repeats, in other words, what the
    person asked her to keep that day isn't filed: every word of it that tells facts
    apart is in that memory, its negation matches, and a fact about the user is never
    taken for one about someone else; `extraction.copy_of`). Telling the model what
    had been saved was measured first and barely helped: 24 copies filed in 18 runs
    became 18, and it found fewer new facts (12 of 15 against 14). The memory
    evaluation now has ten such conversations (four held out from the rule's design):
    over three runs none of the copies the model proposed was filed, no fact said anew
    was dropped, and its other 18 conversations filed as before (no false memory, 13,
    12 and 13 of 13 facts). Learning could save a reworded copy of what a memory tool
    already saved. Told "these days I water them in the evening, and I've added
    basil", she saved that with the correct tool, and learning then added "User waters
    their plants in the evening" and "User has added basil to their balcony garden"
    (seen live at 0.21.5; the session-end learning before it did the same). Learning
    skipped a fact only when she held the same words, once both were normalised.
40. **Fixed in 0.34.11** (a part after the first goes with the last turns read before
    it, as context the extractor is told to extract nothing from; what it restates of
    them isn't filed: a fact that shares no word with what the user said in the part
    but one with those turns, or that copies what the user said in them or what
    learning filed that day, kept, waiting or undone; `extraction.sift`). Telling it
    wasn't enough: sent with them, the model found 14 of 15 facts but restated them 21
    times in 18 runs. A review of the first rule (a fact must share a word with the
    part) found it let restatements through on longer parts and would drop a new fact
    in words of the model's own; both were measured and fixed before release. The
    memory evaluation now has 28 such conversations (seven written after the rules to
    test them): over three runs 76 of their 84 facts were filed, with 4 false
    memories and none on the wrong person; read without the turns before, the same
    conversations gave 49 and 12, 8 of them about the wrong person or nobody named.
    **A chat conversation was learned from in parts, each read on its own** (since
    0.21.5). The
    worker reads what has come since its marker once the model has been free for
    `learning_quiet_s`, so a pause that long splits a conversation, and so does a
    backlog longer than one extraction. The part after a pause didn't see the one
    before it: "she's vegetarian too" after a pause could be missed, or filed as the
    user's own. Session-end learning had the same limit between sessions.
41. **Made rarer in 0.23.0, and measured on 0.34.13 not to mislead qwen3.5:9b. A
    question the documents don't answer is still given passages.** The gate was a similarity floor, and the nearest passage to an
    unanswerable question often scores as close as a right answer does ("sourdough"
    against the focaccia's bake): on the document-search evaluation 6 of 10 such
    questions got passages (7 of 10 at 0.21.5). Since 0.23.0 a cross-encoder rereads the
    candidates against the question and the gate reads its score: 3 of 10, each a near
    miss (the flight out for the flight home). The model sees them fenced and can say
    they don't answer. Measured end to end on 0.34.13: their passages score -5.5 to
    5.4 on the reranker, among the answers' (-6.6 and up), so no floor drops them
    without dropping answers; given them as a chat turn is, qwen3.5:9b said they don't
    answer in 15 of 15 (each asked five times; one answer led with the home policy's
    excess before saying the car's wasn't there). The answer evaluation asks every
    model three such questions since (`g09`-`g11`, 9 of 9 on qwen3.5:9b at 0.34.13
    and at 0.34.14, a decline counted only about what was asked): another model's
    reading is the only defence, and is measured before it is used.
42. **Documents added before 0.22.1 were chunked by older rules.** Before 0.22.0 their
    chunks were stored and embedded without heading paths, and a heading followed by
    another was kept as a chunk holding only that heading. With 0.22.0 such a heading
    at the same level or higher was dropped instead, and so were all but the last of a
    run of `#` comment lines in a code or text file (0.22.1 keeps both). They are found
    as before, and by keyword; removing a document and adding it again chunks it anew.
43. **The reranker reads English best.** ms-marco MiniLM-L-6 was trained on English: it
    still separates right from wrong passages in French, German and Spanish, less
    surely, and not at all in Arabic or Chinese (a wrong passage scored as high as the
    right one), so text in another script isn't reranked and the similarity floor
    decides for it, as before 0.23.0. It also misreads rewordings with no word in
    common: "How often should I get the limescale out?" against "descale" and "Who do I
    get in touch with when something breaks?" against a repairs contact score below its
    floor, two answers the similarity floor had cited (it finds two others instead). A
    multilingual reranker would cover both, at more CPU time per question.

Found building the missing-models notice (0.26.0):

44. **Fixed in 0.34.2** (a call that can't start for a model not on disk, found as the
    start-up finds it, names the model and says how to install it: `models_missing`).
    Chat's half was fixed in 0.34.1: a message without the memory model is answered
    without her memory or the documents, and the answer says so. Using what a missing
    model does failed in general words: a chat message without the memory model ended
    in "Something interrupted that answer", and a call without it, or without a voice
    model, failed its warm-up with "check that the backend is running".
45. **Fixed in 0.34.4** (both refusals measured on Ollama 0.33.3 and given kinds of
    their own: `llm_no_tools` and `llm_cant_run`, `adapters/llm/ollama_errors.py`).
    Only a model that isn't pulled had its own kind (0.26.0). A model without tool
    support answers 400, "does not support tools" (measured with smollm:135m): chat
    and calls always offer tools, so every turn failed with Ask again, which can never
    help. A model that can't load answers 500, "llama-server process has terminated",
    and failed a call's warm-up with "check that the backend is running". A copy of
    qwen3 with a template that has no tools still takes them: 0.33.3 keeps the model's
    own template for its tools.

Found building the launcher and the GPU manager's ledger (0.28.0):

46. **Fixed in 0.34.6** (before a call loads anything, the card's own figure is set
    against what the call would add, her backend's share by Windows' count: too
    little free and the call is refused, saying what must be free and what is, or that
    the card is too small for a call when nothing closed would help; little to spare
    and it starts with a word that she may be slow; `voice/realtime/card_room.py`).
    Measured on 0.34.5 with another process holding memory beside calls of four
    turns: with 246 MiB left over she answered once, then not in 60 s; with 504 her
    first words came 1.5 times later; with 574, as quickly as without. Ollama doesn't
    size itself around another program either: with 10 GiB held it loaded her whole
    model with 4 GB free, so a fixed order at the warm-up wouldn't have helped.
    A call while another program held much of the graphics card was unusable, and
    she couldn't tell. With 4 GiB held by another process (a stand-in for a game), a
    call's final transcription took 16.8 s (median of 4 turns, against about 0.4) and
    her first words came 19.7 s after the question (about 2.4 normally): the card was
    full (15,974 of 16,376 MiB) and the speech models were paged out. The GPU
    manager's budget never tripped, because on Windows `torch.cuda.mem_get_info`
    doesn't count what another program holds idle (nvidia-smi read 6,554 MiB in use,
    a fresh process 1,264; her own reading peaked at 7,070).

Found reviewing 0.30.2:

47. **Fixed in 0.34.2** (every change is kept in `active_voice.json` beside the voices,
    and a kept voice since removed gives way to the default). The voice service started
    on `voice.active_voice_id` from the settings (`veronica-default`), and choosing
    another in the Voice Studio changed only the running process, so every start went
    back to the default.
48. **Fixed in 0.34.1** (learning asks at its own temperature, 0.2: the same three
    conversations, asked 20 times each at each temperature side by side, came back
    empty 8 times in 60 at 0.7 and once at 0.2, and the learning test filed 13, 13 and
    12 of its 13 facts with no false memory, against 11 in each of 0.30.2's last three
    runs; the 7 in 60 below is the earlier run that found it). Learning sometimes returned nothing for a plain fact. At the chat
    temperature
    (0.7), qwen3.5:9b answered an empty list of facts for "I work nights at the
    hospital, so I usually sleep until noon" 2 times in 20, and for the gold set's cello
    and sister conversations 3 and 2 in 20 (7 in 60; 11 in 60 with the instruction's old
    examples, measured 2026-09-26). It cost the learning test 2 of 13 facts in each of
    0.30.2's runs. Extraction shared the chat's temperature
    (`services/memory/learning.py`).

Found checking 0.34.6 live:

49. **Fixed in 0.34.7** (a voice that isn't there is spoken in the voice the model
    comes with, which her default voice is recorded from, as a locked one is; the
    call went live in a data folder without any voice). A call without her default
    voice failed as "check that the backend is running". The default voice is made
    by `scripts/seed_default_voice.py`. A data folder without it (a new one, or one
    whose voices were taken away) started with no problem on screen, and every
    call's warm-up then failed (`VoiceMissing: 'veronica-default'`) with the general
    words, as a missing model did before bug 44.

Found measuring bug 37 (0.34.12):

50. **Made rarer in 0.34.13 (the call's style names the actions to take before
    speaking; measured, bug 50 in ARCHITECTURE §7). On a call she said she'd do it
    instead of doing it.** On the call's prompt and tools, qwen3.5:9b called the right
    tool first for 37 and 46 of the tools evaluation's 102 action requests in two
    runs, where on chat's it did for 97: asked to remember something she answered "Got
    it, I'll remember that" and called nothing (1 of 15), and forgetting, correcting
    and opening an app fared little better. The claim check caught such answers and
    said so after them. Tool choice on calls had been measured on three prompts, when
    tools were first attached. Since 0.34.13: 84 of 102 (chat 98), none called unasked;
    remembering and recalling stay the weakest (9 of 15 each), and the tools
    evaluation measures the call's lane beside chat's. Instructions planted in what
    she reads are followed on a call as in chat now (2 and 3 of 36 in two sets of
    runs, chat 3; none before), each stopped by the kernel's rules.

## Next

The order below is a proposal, not a commitment.

**1. Stabilise.** Fix the open bugs above, starting with the first two, which need the
desktop shell built and so a Rust toolchain. Bugs 25, 29, 33, 34 and 48 were fixed
in 0.34.1, 26, 44 and 47 in 0.34.2, 36 in 0.34.3, 45 in 0.34.4, 38 in 0.34.5, 46 in 0.34.6,
49 in 0.34.7, 11, 13's last part and 14 in 0.34.8, and four of the unnumbered ones
ARCHITECTURE lists in 0.34.9 (the audit total, a prompt nobody could see, a clip that
won't open, the transcript of a sealed clip), 39 in 0.34.10, 40 in 0.34.11, in 0.34.12 the half of 37 that didn't tell the model, 50 made rarer in 0.34.13, and in 0.34.14 a caveat for a line written to her that she passes on (20) and 41 measured not to mislead the default model. (The code left over from the project's
earlier direction was removed in 0.31.2.) Replace the template favicon and app icons. The spoken turn is now timed stage by stage from the user's last
word to the first reply audio, and `scripts/call_probe.py` measures it from outside with
p90 and p95 alongside the median. The baseline is recorded (2.09 s from the last word
at 0.15.1), and the language model's part of it is now small; most of what is left is
the first synthesis (about 1 s), the end-of-speech wait (0.43 s, now decided by the
end-of-turn model since 0.16.0) and the final transcription (0.17 s since 0.15.4). The
end-of-turn model was tuned on synthesised questions and still needs a pass with a real
microphone.

**2. Hardening.** Taint tracking shipped in 0.18.0 (bug 20), the claim check in 0.19.0
(bug 37), the hash-chained audit log in 0.20.0 (its head in 0.21.1), the private
data folder in 0.21.0 (bug 23, completed in 0.21.2), versioned upgrades of her memory
file with a copy kept first in 0.24.0, in 0.25.0 a start that goes on without a
memory file she can't use and says why, in 0.28.0 a launcher with a bounded shutdown
and a ledger of what each model costs the graphics card, in 0.29.0 one data folder for
all of her data (bug 27 with it), in 0.30.0 a repeatable test of instructions
planted in what she reads, in 0.30.1 a chat prompt that always fits her model's window,
in 0.30.2 a start that goes on without a voices file she can't read or a diagnostics
folder she can't write, and in 0.31.0 a notice for an activity log changed outside
Veronica, which can be marked as reviewed. Still to come: a token that only
the desktop shell holds, so another program on the machine can't use the local API
(deferred on 2026-09-25 and documented as a known risk in SECURITY.md: the shell
would have to start and supervise the backend, and it can't be built on the
development machine, which has no Rust toolchain);
a Content-Security-Policy for the desktop shell and a minimal Tauri capability set; an
installer and first-run setup (the models have an install script and a start-up check
since 0.15.7, and a missing one is a notice on screen since 0.26.0; the memory model
loads before the first message since 0.26.0, bug 31; and since 0.27.0 Settings > About
checks the graphics card, memory, disk, Ollama with her model and the model files
against what she was measured using; a first-run screen with the same checks comes
with the installer);
encryption at rest, decided in [ADR 0001](docs/adr/0001-encryption-at-rest.md) (her
memory and voice clips, an optional recovery key, migrated at the first start): her
memory since 0.32.0, her voice clips since 0.33.0 (with the fixes from the review of
0.32.0: two starts at once take turns on the key, a key that doesn't fit its file is
said as that, a rename a FAT disk cut in two is finished, and a system without DPAPI
starts plain and says so), and the optional recovery key since 0.34.0 (Settings >
Privacy; a locked notice offers it). Done.

**Model choice.** Veronica is meant for anyone who prefers local models, including
people whose graphics card can run a much larger model than the one she is tested on.
Today the language model is a setting (`llm.model`: any Ollama model that can call
tools), but only qwen3.5:9b on a 16 GB card has been measured. Making the model truly
switchable means: choosing it in Settings and applying it live; keeping the settings
that depend on it (the context window, the reply limit, thinking) in profiles named by
the card's memory rather than tuned in code; measuring the chat window's token estimate
on each model, since it is calibrated on qwen3.5:9b's tokenizer
(`scripts/token_probe.py`); re-running the evaluations per model; and taking memory
budgets from the ledger of what each model costs. The steps are proposed in
[ADR 0002](docs/adr/0002-model-choice.md), with what assumes the default model today.

**3. Connected.** Web search, an MCP client for local tool servers, and computer use,
all behind the capability kernel, with an allow-list for outbound network access and
the same treatment of fetched content as untrusted data.

**4. Sight.** A vision-language model managed by the GPU manager, so attached images and
the screen can be understood. Needs work on sharing VRAM with the speech models.

**5. Artifacts and skills.** Re-runnable artifacts in chat, and packaged skills that add
capabilities without code changes.

**Later.** A wake word ("Hey Veronica") with an acoustic model; an animated avatar;
reminders and briefings; local usage statistics; memory scoped per project;
conversation search; attachment previews; running code in a real sandbox (WSL2 or a
container) rather than a subprocess. Speech in a second language is not planned: the
app is English only.

## Technical debt

- The recap of a call, which the next call's greeting uses, is still written at chat's
  temperature (`voice/realtime/session_tasks.py`). Learning asks at its own, lower one
  since 0.34.1 (bug 48), measured on extraction only; the recap should be measured the
  same way before it follows.
- Going back to a copy of her memory from before an update is done by hand, by moving
  and renaming files as the notice says; the notice names the files but not the folder
  they are in (by default `data/memory` in the installation). A button that does it
  safely, and shows the folder, would need the backend to let go of the file while it
  runs.
- `services/memory/service.py` is at 299 of its 300 lines since 0.34.8: its next change
  should move the review queue (a candidate added, approved, turned down) into a
  module of its own, as the cap's sweep moved into `prune_store.py`.
- `services/chat/store.py` is at 299 of its 300 lines since 0.34.11 (learning's read of
  the turns before a part), and `tests/test_learning_worker.py` at 500 of its 500: the
  store's next change should move learning's two reads into a module of their own, and
  the worker's next tests go in a file beside it, as `test_learning_earlier.py` did.
- `tests/test_voice_tools.py` is at 498 of its 500 lines since 0.34.13: its next tests
  go in a file beside it.
- `useRealtimeCall.ts` is over the 300-line guideline (346 lines) even after the
  microphone moved into `micPipeline.ts`. (`useChat.ts` was too, at 320, until its frame
  handling moved into `chat/frames.ts` in 0.30.2.)
- Component styles still hold spacing literals copied from the blueprint that are off
  the 4 px scale (6, 10, 14 and 18 px): 45 style files have a margin, padding, gap or
  offset of one of them (counted on 2026-09-26), not the five named before. DESIGN.md allows tokens only: either the scale gains those
  half steps or the values round to it. Values already on the scale were moved to
  tokens in 0.15.1.
- Tailwind is still imported, but since the redesign no screen uses a utility class:
  every component is styled by its CSS module. Removing it is a stack decision.
- The desktop shell still accepts a Shift-only global shortcut written into its settings
  file by hand. Settings refuses one (it would take capital letters from every app);
  `shortcuts.rs` should refuse it too.
- The rebuilt screens have been checked against faked backend replies, a stand-in chat
  socket and real model replies, but not yet in a live call on a real microphone.
- Registering a voice transcribes its clip by path when the voice model needs a
  transcript. Chatterbox doesn't, so the branch is unused, but since the clips are
  sealed (0.33.0) it would read the sealed file: a model that needs a transcript must
  be given the clip opened.
- A voice turn that fails (`llm_unreachable`, `reply_failed`) records no metrics row, so
  failures are missing from the latency figures rather than counted as slow turns.
- The backend address is fixed at `localhost:8000`, written once in
  `frontend/src/lib/api.ts`; a setting for it would let the backend run elsewhere.
- The recorder (`useRecorder.ts`) has no front-end tests of its own; the rest of the
  front end has component tests (37 files, 275 tests at 0.15.1).
- Every page is in one script, 505 kB at 0.27.0, just over Vite's 500 kB advisory.
  Loading pages on demand (React Router's `lazy`, as the design page already is in
  development) would split it; dynamic imports are still to be tried in the packaged
  shell.

## Releasing

1. Add an entry at the top of `CHANGELOG.yaml`: shipped changes only, in plain language,
   with measured numbers.
2. Set the same version and label in `backend/app/core/version.py`.
3. Bump `backend/pyproject.toml`, `frontend/package.json`, `frontend/src-tauri/Cargo.toml`
   and `frontend/src-tauri/tauri.conf.json`, then refresh the lock files (`uv lock`,
   `npm install`, `cargo check`).
4. Run all the gates. `tests/test_version_drift.py` fails if any of the versions disagree.
