# ── Road Sign Detector C++ — Multi-stage Docker build ──
# Self-contained: reads camera MJPEG → ONNX inference → POSTs to Node.js
# No Python dependency.

# ─────────── Build stage ───────────
FROM debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    wget \
    ca-certificates \
    libopencv-dev \
    linux-libc-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Install ONNX Runtime (aarch64 / x86_64) ──
ARG ONNXRUNTIME_VERSION=1.17.1
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "aarch64" ]; then \
        ORT_URL="https://github.com/microsoft/onnxruntime/releases/download/v${ONNXRUNTIME_VERSION}/onnxruntime-linux-aarch64-${ONNXRUNTIME_VERSION}.tgz"; \
    else \
        ORT_URL="https://github.com/microsoft/onnxruntime/releases/download/v${ONNXRUNTIME_VERSION}/onnxruntime-linux-x64-${ONNXRUNTIME_VERSION}.tgz"; \
    fi && \
    wget -q "$ORT_URL" -O /tmp/ort.tgz && \
    mkdir -p /opt/onnxruntime && \
    tar -xzf /tmp/ort.tgz -C /opt/onnxruntime --strip-components=1 && \
    rm /tmp/ort.tgz

# Copy C++ source
COPY src/ /build/src/
COPY CMakeLists.txt /build/

# Build
WORKDIR /build
RUN mkdir -p build && cd build && \
    cmake .. \
        -DCMAKE_BUILD_TYPE=Release \
        -DONNXRUNTIME_DIR=/opt/onnxruntime && \
    make -j$(nproc)

# ─────────── Runtime stage ───────────
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libopencv-core406 \
    libopencv-imgcodecs406 \
    libopencv-imgproc406 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy ONNX Runtime shared libs
COPY --from=builder /opt/onnxruntime/lib/ /usr/local/lib/
RUN ldconfig

# Copy built binary
COPY --from=builder /build/build/roadsign_server /usr/local/bin/

# Copy model weights
COPY best_yolo.onnx /app/detect-road-sign/best_yolo.onnx

# Environment defaults
ENV MODEL_PATH=/app/detect-road-sign/best_yolo.onnx
ENV CONF_THRESHOLD=0.5
ENV NMS_IOU_THRESHOLD=0.45
ENV NUM_THREADS=2
ENV INPUT_SIZE=640
ENV DETECT_PORT=9001
ENV DETECT_INTERVAL_MS=300
ENV CAMERA_MODE=mjpeg
ENV V4L2_DEVICE=/dev/video0
ENV V4L2_WIDTH=640
ENV V4L2_HEIGHT=480
ENV CAMERA_MJPEG_URL=http://hardware:8765/stream
ENV BACKEND_HOST=backend
ENV BACKEND_PORT=5000
ENV OMP_NUM_THREADS=2
ENV GOMP_CPU_AFFINITY=2-3
ENV USE_FP16=false

EXPOSE 9001

CMD ["roadsign_server"]
