"""tb-tts: OmniVoice text-to-speech HTTP wrapper (CPU).

The model loads once on first request and stays resident — a cold load pulls ~3 GB
of weights from HuggingFace into /models, a warm load is ~1.5 s. Synthesis is
serialised behind a lock: the model is not re-entrant and concurrent requests on a
CPU box would only fight each other for the same cores.

Voice cloning is per request. Send a 3-10 s reference clip and the reply speaks in
that voice. Supplying the reference's transcript is optional but worth doing — the
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
MODEL_ID = "k2-fsa/OmniVoice"

app = FastAPI()
_model = None
_model_lock = asyncio.Lock()
_infer_lock = asyncio.Lock()


async def get_model():
    """Load once, keep resident. First call downloads weights into /models."""
    global _model
    if _model is not None:
        return _model
    async with _model_lock:
        if _model is not None:
            return _model
        from omnivoice import OmniVoice

        loop = asyncio.get_event_loop()
        _model = await loop.run_in_executor(
            None,
            lambda: OmniVoice.from_pretrained(
                MODEL_ID, device_map="cpu", dtype=torch.float32
            ),
        )
        return _model


@app.post("/synthesize")
async def synthesize(
    text: str = Form(...),
    steps: int = Form(32),
    reference: UploadFile | None = File(None),
    reference_text: str | None = Form(None),
    normalize_text: bool = Form(False),
):
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
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
