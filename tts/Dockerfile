# tb-tts: OmniVoice text-to-speech, CPU-only.
#
# Deliberately CPU: this stack's GPU is AMD, and OmniVoice is a PyTorch model with
# no ROCm path (it ships CUDA / Apple MPS / Intel XPU backends). Rather than leave
# TTS off the toolbox entirely, it runs on CPU — slower than real time, but fine
# for batch narration, which is what agents actually ask for. See README for numbers.
#
# The dependency list is plain torch + transformers with no flash-attn and no vllm,
# which is exactly why a CPU build is possible at all.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch wheels: ~200 MB instead of the ~2.5 GB CUDA build, and the CUDA
# one would be dead weight here anyway.
RUN pip install --no-cache-dir \
        torch torchaudio --index-url https://download.pytorch.org/whl/cpu

RUN git clone --depth 1 https://github.com/k2-fsa/OmniVoice.git /src/OmniVoice \
    && pip install --no-cache-dir -e /src/OmniVoice

COPY requirements.txt server.py /app/
RUN pip install --no-cache-dir -r requirements.txt

# Model weights (~3 GB) download on first synthesis and persist in the volume.
# HF_HOME points into it so a container rebuild doesn't re-download.
ENV HF_HOME=/models
ENV OMP_NUM_THREADS=8
VOLUME ["/models"]

EXPOSE 8000

CMD ["python3", "server.py"]
