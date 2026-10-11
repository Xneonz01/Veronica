# Third-party notices

The MIT licence in `LICENSE` covers this repository's own source code only.

Veronica does not bundle model weights. They are downloaded on first use, or served by a
locally installed Ollama, and each carries its own licence, which governs what you may do
with the model independently of this repository's licence.

## Models

| Model | Role | Licence (check before relying on it) |
|---|---|---|
| Whisper `large-v3-turbo`, via faster-whisper | streaming speech recognition | MIT |
| Chatterbox Turbo (Resemble AI) | speech synthesis and zero-shot voice cloning | MIT |
| Silero VAD | voice activity detection | MIT |
| `intfloat/multilingual-e5-base` | text embeddings | MIT |
| Qwen3.5 9B, served by Ollama | language model | Apache 2.0 |

## Main libraries

| Library | Role | Licence |
|---|---|---|
| FastAPI, Starlette, uvicorn, Pydantic, httpx | backend framework | MIT / BSD |
| PyTorch, torchaudio | model runtime | BSD-style |
| faster-whisper, CTranslate2 | speech recognition runtime | MIT |
| sentence-transformers | embedding runtime | Apache 2.0 |
| sqlite-vec | vector search inside SQLite | Apache 2.0 / MIT |
| aiosqlite | async SQLite access | MIT |
| sqlcipher3-wheels (the `sqlcipher3` binding) | her memory file, encrypted at rest | zlib |
| SQLCipher, bundled in sqlcipher3-wheels | SQLite with every page encrypted | BSD-3-Clause (Zetetic LLC) |
| OpenSSL 3.6.0, linked into sqlcipher3-wheels | SQLCipher's cryptography | Apache 2.0 |
| cryptography | her voice clips, sealed at rest (AES-256-GCM) | Apache 2.0 or BSD-3-Clause |
| OpenSSL 4.0.2, linked into cryptography | its cryptography | Apache 2.0 |
| pypdf | PDF text extraction | BSD-3-Clause |
| soundfile, NumPy, PyYAML | audio files, arrays, configuration | BSD / MIT |
| React, React Router, Vite, Tailwind CSS | front end | MIT |
| Inter (`@fontsource-variable/inter`) | the interface typeface, bundled with the app | SIL Open Font License 1.1 |
| Lucide (`lucide-react`) | interface icons | ISC |
| Tauri 2 and its global-shortcut and autostart plugins | desktop shell | MIT / Apache 2.0 |
| Ollama | local model server (installed separately, not bundled) | MIT |

The stack was chosen to avoid non-commercial restrictions. If you swap in a different
model, check its licence first: several popular open speech models (XTTS-v2, F5-TTS,
Voxtral) are non-commercial, and Piper moved from MIT to GPL-3.0.

Voice clips you add to the Voice Studio are your responsibility: only clone a voice you
have the right to use.

Licences change. Confirm each one at its source before any commercial use.
