# tb-tts: OmniVoice text-to-speech, CPU-only.
#
# OmniVoice is a PyTorch model with CUDA / Apple MPS / Intel XPU backends and no
# AMD (ROCm) path. This image installs CPU-only PyTorch, so it runs on any machine:
# slower than real time, but fine for narration and voiceover. See the README for
# numbers.
#
# The dependency list is plain torch + transformers with no flash-attn and no vllm,
# which is exactly why a CPU build is possible at all.

# Frozen: base by digest, torch/OmniVoice/deps at exact versions. See docs/operations.md.
FROM python:3.12.13-slim-trixie@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch wheels: ~200 MB instead of the ~2.5 GB CUDA build, and the CUDA
# one would be dead weight here anyway. torch and torchaudio come only from the
# PyTorch CPU index (requirements-torch.lock), everything else only from PyPI
# (requirements.lock), so no package can be taken from the other index. Every file
# is checked against the sha256 listed for it.
COPY requirements-torch.lock requirements.lock /app/
RUN pip install --no-cache-dir --require-hashes --no-deps \
        --index-url https://download.pytorch.org/whl/cpu -r requirements-torch.lock \
    && pip install --no-cache-dir --require-hashes -r requirements.lock

ARG OMNIVOICE_COMMIT=468e927ba3716cd8dd86421148dfb3046e9f9d7b
RUN git clone https://github.com/k2-fsa/OmniVoice.git /src/OmniVoice \
    && git -C /src/OmniVoice checkout --detach "$OMNIVOICE_COMMIT" \
    && pip install --no-cache-dir --no-deps -e /src/OmniVoice

COPY server.py /app/

# Model weights (~3 GB) download on first synthesis and persist in the volume.
# HF_HOME points into it so a container rebuild doesn't re-download.
ENV HF_HOME=/models
ENV OMP_NUM_THREADS=8
VOLUME ["/models"]

EXPOSE 8000

CMD ["python3", "server.py"]
