"""Voice Studio REST: list / create / activate / preview / delete voices.

Uploaded clips and in-app recordings both arrive as multipart WAV files on
`POST /voices` (the frontend encodes recorded PCM to WAV), so there is no separate
record route.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from app.api.refusals import Refusal
from app.api.uploads import read_or_refuse
from app.services.voice.voice_studio import VoiceStudio, VoiceStudioError
from app.services.voice.voices import Voice, VoiceMissing

router = APIRouter(prefix="/voices", tags=["voices"])

# A voice keeps 20 seconds of its clips; more than a few is only more to read.
MAX_CLIPS = 10


def _studio(request: Request) -> VoiceStudio:
    studio: VoiceStudio | None = request.app.state.voice_runtime.studio
    if studio is None:  # pragma: no cover - always wired in production
        raise HTTPException(status_code=503, detail="Voice studio is unavailable.")
    return studio


# File locations stay on the server. The UI never needs them, and an install under the
# user's profile would put the account name in every response.
_SERVER_ONLY = {"reference_wav_path", "embedding_path"}


def _public(voice: Voice) -> dict[str, Any]:
    return voice.model_dump(mode="json", exclude=_SERVER_ONLY)


def _refused(exc: VoiceStudioError) -> Refusal:
    """A voice that isn't there is a 404; anything else wrong with one, a 422."""
    return Refusal(404 if exc.code == "voice_missing" else 422, exc.code, str(exc))


@router.get("")
async def list_voices(request: Request) -> dict[str, Any]:
    studio = _studio(request)
    active = request.app.state.voice_runtime.voice_service.active_voice_id
    return {
        "active": active,
        "voices": [
            {**_public(voice), "active": voice.voice_id == active} for voice in studio.list_voices()
        ],
    }


@router.post("")
async def create_voice(
    request: Request,
    name: Annotated[str, Form()],
    consent: Annotated[bool, Form()],
    files: Annotated[list[UploadFile], File()],
    source: Annotated[str, Form()] = "upload",
) -> dict[str, Any]:
    if not consent:
        raise Refusal(
            400, "consent_missing", "Please confirm you have the right to use this voice."
        )
    if len(files) > MAX_CLIPS:
        raise Refusal(422, "too_many_clips", f"A voice takes up to {MAX_CLIPS} clips.")
    studio = _studio(request)
    # Decoded in memory: no plain copy of someone's voice is written out to be left
    # behind by a crash (ADR 0001). Starlette spools a file over 1 MB to a temporary
    # one, which Windows deletes when it is closed, whether or not the process ends.
    uploads = [await read_or_refuse(upload) for upload in files]
    try:
        return _public(await studio.create_from_clips(name, uploads, source=source))
    except VoiceStudioError as exc:
        raise _refused(exc) from exc


@router.post("/{voice_id}/activate")
async def activate_voice(voice_id: str, request: Request) -> dict[str, str]:
    voice_service = request.app.state.voice_runtime.voice_service
    try:
        # set_active warms the voice's conditioning, so check the reference length
        # first: a too-short clip would fail with the model's raw AssertionError.
        await _studio(request).ensure_reference_ok(voice_id)
        await voice_service.set_active(voice_id)
    except VoiceStudioError as exc:
        raise _refused(exc) from exc
    except VoiceMissing as exc:
        raise Refusal(404, "voice_missing", "That voice no longer exists.") from exc
    return {"active": voice_id}


@router.post("/{voice_id}/preview")
async def preview_voice(voice_id: str, request: Request) -> Response:
    studio = _studio(request)
    try:
        wav = await studio.preview(voice_id)
    except VoiceStudioError as exc:
        raise _refused(exc) from exc
    finally:
        # A preview loads the voice model; outside a call nothing let it go until the
        # next call ended (bug 13). A preview that failed partway may have loaded it too.
        release_after_s = request.app.state.settings.realtime.release_models_after_s
        request.app.state.voice_runtime.calls.release_later(release_after_s)
    return Response(content=wav, media_type="audio/wav")


@router.delete("/{voice_id}")
async def delete_voice(voice_id: str, request: Request) -> dict[str, str]:
    studio = _studio(request)
    try:
        fell_back = await studio.delete(voice_id)
    except VoiceStudioError as exc:
        raise _refused(exc) from exc
    return {"deleted": voice_id, "active": fell_back}
