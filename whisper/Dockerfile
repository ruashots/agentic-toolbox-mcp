# whisper.cpp HTTP server with AMD Vulkan backend (RDNA1+ 5700 XT)
# Multi-stage: builder compiles whisper.cpp with Vulkan, runtime ships drivers + slim python wrapper.

# Frozen: base by digest, whisper.cpp at an exact commit, Python deps at exact versions.
# See docs/operations.md.
FROM ubuntu:24.04@sha256:73b999393d07f2bf65a40373778a44dc9a6d54ad7718d29247d8ff9413c4d6a8 AS builder

# Ubuntu 24.04 ships glslc in main. Older Ubuntu/Debian don't — would need the LunarG SDK repo.
# whisper.cpp's Vulkan backend needs glslc to compile its SPIR-V compute shaders at build time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake build-essential libvulkan-dev glslc \
        spirv-headers glslang-dev libshaderc-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Last whisper.cpp commit before the image that ran from 2026-05-28 to 2026-11-19 was built.
ARG WHISPER_CPP_COMMIT=c932729a304f7d9eb5354afa38624cfa86a780cf
RUN git clone https://github.com/ggml-org/whisper.cpp /src \
    && git -C /src checkout --detach "$WHISPER_CPP_COMMIT"
WORKDIR /src
RUN cmake -B build \
        -DGGML_VULKAN=1 \
        -DBUILD_SHARED_LIBS=OFF \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_BUILD_EXAMPLES=ON \
    && cmake --build build -j4 --target whisper-cli
    # -j4 (not -j): heavy templated C++ + Vulkan headers can spike to ~3GB per cc1plus.
    # All-cores parallel will OOM on an 8 GB machine. -j4 keeps the build under that.

FROM ubuntu:24.04@sha256:73b999393d07f2bf65a40373778a44dc9a6d54ad7718d29247d8ff9413c4d6a8

# Match builder glibc/libstdc++. Whisper-cli built on 24.04 needs GLIBC_2.38 + GLIBCXX_3.4.32.
# Runtime: Mesa RADV vulkan driver for AMD + ffmpeg for audio normalization + python wrapper.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libvulkan1 mesa-vulkan-drivers vulkan-tools \
        ffmpeg \
        python3 python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /src/build/bin/whisper-cli /usr/local/bin/whisper-cli

COPY requirements.lock server.py /app/
RUN pip install --no-cache-dir --break-system-packages -r requirements.lock

# Models cached in /models (volume mount). First call to an uncached model downloads it.
VOLUME ["/models"]

EXPOSE 8000

CMD ["python3", "server.py"]
