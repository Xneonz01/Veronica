# ADR 0002: Choosing her language model

- **Status:** proposed, 2026-09-26. Nothing in it is built yet.
- **Changes a locked decision:** ARCHITECTURE §21, decision 3 fixes one model,
  qwen3.5:9b, for chat, calls and learning. This keeps one model at a time but makes
  which one a choice, which is why it is an ADR rather than a task.

## Context

Veronica is meant for anyone who prefers a local model, including people whose
graphics card can run a much larger one than she is tested with (a model of about 30
billion parameters on a card with 24 to 32 GB, say). The model is already a setting,
`llm.model`: any model Ollama serves that can call tools. But only qwen3.5:9b on a 16 GB
card has been measured, and several parts of her were sized for it.

## What assumes the default model today

Found by reading the code on 2026-09-26:

| Where | What it assumes | What another model can break |
|---|---|---|
| `adapters/llm/ollama_client.py` | `think` is sent on every request (false by default), which is how Qwen3 models are kept from reasoning at length before they answer | Whether Ollama accepts `think` for a model that can't reason isn't known here: no such model is installed on the development machine. Ollama 0.33.3 lists what each model can do (`/api/tags`, read by `adapters/llm/ollama_models.py`), which could decide it |
| `core/config_llm.py` | `num_ctx` 8,192 and `num_predict` 2,048, sized from qwen3.5:9b's largest prompt (5,306 tokens) and what a 16 GB card has left beside the speech models | A larger model needs more memory for the same window; a larger card can afford a longer one |
| `adapters/llm/tokens.py` | The chat window's token estimate follows qwen3.5:9b's tokenizer (measured on the 39 texts of `eval/tokens.py`) | Another tokenizer can count more than the estimate: the window would then let through a prompt the server cuts from the front. `scripts/token_probe.py` measures it |
| `services/chat/learning_worker.py` | One extraction reads `(num_ctx − 1,024) × 3` characters, 3 characters a token from Qwen's measurements | Fewer characters a token than that and a long conversation overflows the extraction's window |
| `services/persona/`, the tool descriptions | Worded and checked against qwen3.5:9b with the evaluations (`backend/eval`: tools, learning, answers, planted instructions) | Another model can choose tools, file memories or follow planted text differently; the evaluations run per model, but nothing requires running them |
| `doctor.py`, `voice/realtime/card_room.py` | What a call needs, 14,168 MiB of graphics memory and 6,327 MiB of RAM, was measured with qwen3.5:9b and the speech models, and so were her backend's share at a call's peak (6,795 MiB) and the room a call keeps beside it | A larger model needs more than the check in Settings says; until Ollama has loaded it, a call counts only its files |
| `configs/profile.gpu_16gb.yaml` | Named by the card's memory since 0.30.2, but holds only the memory budget | Nothing chooses a model, window or reply limit per card |
| The app | No way to choose the model, and a settings change needs her backend restarted | Against the rule that every setting applies live |

What already holds for any model: the model sits behind the Ollama adapter; tools are
offered through native tool calling, and Settings checks that the model can use tools
where Ollama says so; the chat window fits whatever `num_ctx` says; evaluation results
are stored per model.

## Options

1. **Settings only.** Document which settings to change for a larger model. Nothing to
   build, but every row above stays wrong for another model, silently.
2. **Measured per model, chosen by file.** A record per model, written by the probes
   (`scripts/prompt_probe.py`, `scripts/token_probe.py`, the call probe): whether the
   token estimate holds for it and by how much to scale it if not, its largest prompts,
   what a call needs. The window, the learning budget and the check in Settings read
   the record of the model in use, and a model never measured is treated cautiously (a
   larger margin on the estimate, a warning in Settings). Profiles by card size choose
   a model, a window and a reply limit together.
3. **Option 2, and a choice in Settings.** Settings lists the models Ollama has that
   can call tools, with what each needs of this computer, and switches to one without a
   restart: the next request uses it, and the check runs again.

## Recommendation

Option 3, built in steps that each stand on their own:

1. **Ask each model only what it can do.** Send `think` only to a model that lists
   reasoning among what it can do, and say plainly in Settings when a model can't call
   tools. Small, and removes the one assumption that could fail a request outright.
2. **A record per model.** The probes write it; the window's estimate, the learning
   budget and the check in Settings read it, and an unmeasured model gets a margin.
3. **Profiles by card size.** `gpu_16gb`, and larger ones once measured on a larger card.
4. **The choice in Settings,** applied live.
5. **Evaluations before a recommendation.** A model is suggested for a card only after
   the evaluations have run on it.

## Consequences

- Each step is measured on the development machine's 16 GB card first; a larger model
  can only be measured on a larger card, so its record waits for one.
- Decision 3 in ARCHITECTURE §21 becomes "one model at a time, chosen per computer".
- Nothing changes for someone who keeps the default model.
