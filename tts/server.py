"""tb-tts: OmniVoice text-to-speech HTTP wrapper (CPU).

The model loads once on first request and stays resident: the first load ever pulls
~3 GB of weights from Hugging Face into /models, a warm load is ~1.5 s. The model is
pinned to one Hugging Face revision: it is downloaded once at that revision, and
after that loading never contacts Hugging Face, so a new upstream model is never
taken by itself and the service starts without internet access. Synthesis is
serialised behind a lock: the model is not re-entrant and concurrent requests on a
CPU box would only fight each other for the same cores.

Voice cloning is per request. Send a 3-10 s reference clip and the reply speaks in
that voice. Supplying the reference's transcript is optional but worth doing: the
model otherwise runs Whisper to transcribe it, which on CPU costs more than the
synthesis itself.
"""

import asyncio
import io
import tempfile
import time
from pathlib import Path

import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

SAMPLE_RATE = 24000
# Pinned revisions. To move to a newer one on purpose, see docs/operations.md.
MODEL_ID = "k2-fsa/OmniVoice"
MODEL_REVISION = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"
# Only used to transcribe a voice-cloning clip when no reference_text is sent.
ASR_ID = "openai/whisper-large-v3-turbo"
ASR_REVISION = "41f01f3fe87f28c78e2fbf8b568835947dd65ed9"

app = FastAPI()
_model = None
_asr_loaded = False
_model_lock = asyncio.Lock()
_infer_lock = asyncio.Lock()


def _pinned(repo_id: str, revision: str) -> str:
    """Local path of a model at one exact revision in the /models volume.

    Downloads it the first time it is needed. Once it is in the volume this never
    contacts Hugging Face.
    """
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        return snapshot_download(repo_id, revision=revision, local_files_only=True)
    except LocalEntryNotFoundError:
        return snapshot_download(repo_id, revision=revision)


async def get_model():
    """Load once, keep resident. The first call ever downloads the weights into /models."""
    global _model
    if _model is not None:
        return _model
    async with _model_lock:
        if _model is not None:
            return _model
        from omnivoice import OmniVoice

        loop = asyncio.get_event_loop()
        try:
            _model = await loop.run_in_executor(
                None,
                lambda: OmniVoice.from_pretrained(
                    _pinned(MODEL_ID, MODEL_REVISION), device_map="cpu", dtype=torch.float32
                ),
            )
        except Exception as exc:
            raise HTTPException(503, f"could not load the voice model: {exc}") from exc
        return _model


@app.post("/synthesize")
async def synthesize(
    text: str = Form(...),
    steps: int = Form(32),
    reference: UploadFile | None = File(None),
    reference_text: str | None = Form(None),
    normalize_text: bool = Form(False),
):
    global _asr_loaded
    if not text.strip():
        raise HTTPException(400, "text is empty")
    if steps < 1 or steps > 64:
        raise HTTPException(400, "steps must be between 1 and 64")

    model = await get_model()

    with tempfile.TemporaryDirectory() as tmp_dir:
        kwargs: dict = {"text": text, "num_step": steps}
        if normalize_text:
            kwargs["normalize_text"] = True

        if reference is not None:
            ref_path = Path(tmp_dir) / "reference.wav"
            ref_path.write_bytes(await reference.read())
            kwargs["ref_audio"] = str(ref_path)
            if reference_text:
                kwargs["ref_text"] = reference_text

        started = time.time()
        async with _infer_lock:
            loop = asyncio.get_event_loop()
            if reference is not None and not reference_text and not _asr_loaded:
                # Load the pinned transcription model ourselves; left to OmniVoice it
                # would download the newest one.
                try:
                    await loop.run_in_executor(
                        None, lambda: model.load_asr_model(_pinned(ASR_ID, ASR_REVISION), device="cpu")
                    )
                except Exception as exc:
                    raise HTTPException(503, f"could not load the transcription model: {exc}") from exc
                _asr_loaded = True
            try:
                audio = await loop.run_in_executor(None, lambda: model.generate(**kwargs))
            except Exception as exc:  # surface the real cause, not a bare 500
                raise HTTPException(500, f"synthesis failed: {exc}") from exc
        elapsed = time.time() - started

    samples = audio[0]
    duration = len(samples) / SAMPLE_RATE

    buf = io.BytesIO()
    sf.write(buf, samples, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    payload = buf.getvalue()

    return Response(
        content=payload,
        media_type="audio/wav",
        headers={
            "X-Audio-Seconds": f"{duration:.2f}",
            "X-Generate-Seconds": f"{elapsed:.2f}",
            "X-Realtime-Factor": f"{elapsed / duration:.2f}" if duration else "0",
            "X-Sample-Rate": str(SAMPLE_RATE),
            "X-Cloned": "1" if reference is not None else "0",
        },
    )


@app.get("/health")
async def health():
    cached = Path("/models").exists() and any(Path("/models").rglob("*.safetensors"))
    return {
        "status": "ok",
        "model": MODEL_ID,
        "device": "cpu",
        "sample_rate": SAMPLE_RATE,
        "weights_cached": cached,
        "model_loaded": _model is not None,
    }


if __name__ == "__main__":
    # access_log=False: the toolbox keeps no record of requests (privacy rule).
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning", access_log=False)
