"""tb-whisper: whisper.cpp + AMD Vulkan HTTP wrapper.

Lazy-downloads models on first use, caches in /models volume. The 5700 XT (or any
Vulkan-capable AMD GPU) is auto-detected by whisper.cpp at runtime — no per-model
config needed.
"""

import asyncio
import json
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
import uvicorn

MODELS_DIR = Path("/models")
MODELS_DIR.mkdir(exist_ok=True)

# whisper.cpp ggml model URLs on HuggingFace
MODEL_URLS = {
    "tiny": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin",
    "tiny.en": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin",
    "base": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
    "base.en": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin",
    "small": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
    "small.en": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin",
    "medium": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin",
    "medium.en": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin",
    "large-v3": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin",
    "large-v3-turbo": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
}

app = FastAPI()
download_lock = asyncio.Lock()


async def ensure_model(model: str) -> Path:
    if model not in MODEL_URLS:
        raise HTTPException(400, f"Unknown model '{model}'. Valid: {sorted(MODEL_URLS)}")
    path = MODELS_DIR / f"ggml-{model}.bin"
    if path.exists():
        return path
    async with download_lock:
        if path.exists():
            return path
        url = MODEL_URLS[model]
        tmp = path.with_suffix(".tmp")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: urllib.request.urlretrieve(url, tmp))
        tmp.rename(path)
        return path


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form("base.en"),
    language: str | None = Form(None),
):
    model_path = await ensure_model(model)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        in_path = tmp / "input.bin"
        wav_path = tmp / "input.wav"

        content = await file.read()
        in_path.write_bytes(content)

        # whisper.cpp needs 16 kHz mono PCM — normalize via ffmpeg.
        ff = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(in_path),
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                str(wav_path),
            ],
            capture_output=True, text=True,
        )
        if ff.returncode != 0:
            raise HTTPException(400, f"ffmpeg decode failed: {ff.stderr[:500]}")

        cmd = [
            "whisper-cli",
            "-m", str(model_path),
            "-f", str(wav_path),
            "--output-json",
            "--no-prints",
        ]
        if language:
            cmd += ["-l", language]

        run = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if run.returncode != 0:
            raise HTTPException(500, f"whisper-cli failed: {run.stderr[:1500]}")

        # whisper-cli writes <wav>.json next to the input
        json_path = wav_path.with_suffix(".wav.json")
        if not json_path.exists():
            # Fallback: text-only output via stdout
            return {"text": run.stdout.strip(), "model": model, "segments": []}

        data = json.loads(json_path.read_text())
        segments = data.get("transcription", [])
        text = " ".join(s.get("text", "").strip() for s in segments).strip()
        return {
            "text": text,
            "model": model,
            "language": data.get("result", {}).get("language"),
            "segments": [
                {
                    "start": s.get("offsets", {}).get("from", 0) / 1000.0,
                    "end": s.get("offsets", {}).get("to", 0) / 1000.0,
                    "text": s.get("text", "").strip(),
                }
                for s in segments
            ],
        }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "models_available": sorted(MODEL_URLS),
        "models_cached": sorted(p.stem.replace("ggml-", "") for p in MODELS_DIR.glob("ggml-*.bin")),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
