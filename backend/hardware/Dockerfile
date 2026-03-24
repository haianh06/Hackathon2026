# Hardware Daemon Dockerfile - Python (standalone container)
# Runs: motor control, camera stream, RFID, line-following
# Road sign detection is handled by separate C++ container (roadsign)
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install build tools + hardware libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    i2c-tools \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install base Python dependencies first (better layer caching)
COPY hardware/requirements.txt ./hardware/requirements.txt
RUN pip install --no-cache-dir \
    "lgpio" \
    "spidev" \
    "aiohttp>=3.9.0" \
    "python-socketio[asyncio_client]>=5.11.0" \
    "aiohttp-cors>=0.7.0" \
    "pyserial>=3.5" \
    "opencv-python-headless>=4.8.0" \
    "numpy>=1.24.0"

# PyTorch (CPU-only) — still needed for UNet line follower
RUN pip install --no-cache-dir --timeout 600 --retries 5 \
    "torch>=2.0.0" --extra-index-url https://download.pytorch.org/whl/cpu

# Copy model weights (UNet line follower only; road sign uses C++ container)
COPY line_following/best_unet.pth ./line_following/best_unet.pth

# Copy canny edge detection module
COPY canny-edge-detection-main/ ./canny-edge-detection-main/

# Copy hardware source code
COPY hardware/ ./hardware/

# Set thread limits via env vars (prevents CPU overload)
ENV OMP_NUM_THREADS=2
ENV MKL_NUM_THREADS=2
ENV OPENBLAS_NUM_THREADS=2
ENV TORCH_NUM_THREADS=2
ENV PYTHONUNBUFFERED=1

EXPOSE 8765

CMD ["python3", "hardware/daemon.py"]
