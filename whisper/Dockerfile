# whisper.cpp HTTP server with AMD Vulkan backend (RDNA1+ 5700 XT)
# Multi-stage: builder compiles whisper.cpp with Vulkan, runtime ships drivers + slim python wrapper.

FROM ubuntu:24.04 AS builder

# Ubuntu 24.04 ships glslc in main. Older Ubuntu/Debian don't — would need the LunarG SDK repo.
# whisper.cpp's Vulkan backend needs glslc to compile its SPIR-V compute shaders at build time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake build-essential libvulkan-dev glslc \
        spirv-headers glslang-dev libshaderc-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/ggerganov/whisper.cpp /src
WORKDIR /src
RUN cmake -B build \
        -DGGML_VULKAN=1 \
        -DBUILD_SHARED_LIBS=OFF \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_BUILD_EXAMPLES=ON \
    && cmake --build build -j4 --target whisper-cli
    # -j4 (not -j): heavy templated C++ + Vulkan headers can spike to ~3GB per cc1plus.
    # All-cores parallel will OOM in an 8GB LXC. -j4 keeps build under the cap.

FROM ubuntu:24.04

# Match builder glibc/libstdc++. Whisper-cli built on 24.04 needs GLIBC_2.38 + GLIBCXX_3.4.32.
# Runtime: Mesa RADV vulkan driver for AMD + ffmpeg for audio normalization + python wrapper.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libvulkan1 mesa-vulkan-drivers vulkan-tools \
        ffmpeg \
        python3 python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /src/build/bin/whisper-cli /usr/local/bin/whisper-cli

COPY requirements.txt server.py /app/
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Models cached in /models (volume mount). First call to an uncached model downloads it.
VOLUME ["/models"]

EXPOSE 8000

CMD ["python3", "server.py"]
