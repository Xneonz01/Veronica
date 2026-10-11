"""Seed the placeholder default voice and validate the real Chatterbox API.

Generates a neutral reference clip from Chatterbox's own (MIT) default voice —
license-clean, offline, no game/celebrity/actor voice — saves it into the voices
directory, sealed as every clip is (ADR 0001), and registers it as
``veronica-default``. Doubles as the real-model check that
``prepare_conditionals``/``conds``/``generate`` behave as the adapter expects,
the clip opened into memory as the adapter opens it. Run once:
uv run python -m scripts.seed_default_voice
"""

from __future__ import annotations

import io

import soundfile as sf

from app.adapters.tts.chatterbox_stream import chatterbox_loader
from app.core.config import get_settings
from app.services.voice.clips import Clips
from app.services.voice.voices import Voice, VoiceRegistry

VOICE_ID = "veronica-default"
_REFERENCE_TEXT = (
    "This is a neutral reference voice for Veronica. "
    "It is a clean, dry recording used only to prove voice cloning."
)


def main() -> None:
    settings = get_settings()
    registry = VoiceRegistry(settings.voice.voices_dir)
    clips = Clips.open(registry.directory)
    if clips.locked is not None:
        raise SystemExit(f"Her voices are locked ({clips.locked}): nothing was written.")
    ref_path = registry.directory / f"{VOICE_ID}.wav"

    print("Loading Chatterbox Turbo ...")
    model = chatterbox_loader(settings.tts)()

    print("Generating neutral reference clip (default voice) ...")
    wav = model.generate(_REFERENCE_TEXT)
    samples = wav.squeeze(0).detach().cpu().numpy()
    clip = io.BytesIO()
    sf.write(clip, samples, model.sr, format="WAV", subtype="PCM_16")
    with ref_path.open("wb") as handle:
        clips.write(handle, ref_path, clip.getvalue())
    print(f"Saved reference clip -> {ref_path} ({len(samples) / model.sr:.1f}s @ {model.sr} Hz)")

    print("Validating prepare_conditionals + conds + cloned generate ...")
    model.prepare_conditionals(io.BytesIO(clips.read(ref_path)))
    if model.conds is None:
        raise RuntimeError("prepare_conditionals did not set model.conds")
    cloned = model.generate("Veronica is online.")
    print(f"Cloned generate OK | shape={tuple(cloned.shape)} sr={model.sr}")

    registry.add(
        Voice(
            voice_id=VOICE_ID,
            name="Veronica (placeholder)",
            reference_wav_path=str(ref_path),
            prompt_text=_REFERENCE_TEXT,
            embedding_path=str(registry.embedding_path_for(VOICE_ID)),
        )
    )
    print(f"Registered '{VOICE_ID}' in {registry.directory / 'voices.json'}")
    print("SEED OK")


if __name__ == "__main__":
    main()
