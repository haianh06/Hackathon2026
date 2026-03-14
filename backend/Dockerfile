# Backend Dockerfile - Node.js + Python hardware daemon
# Using Debian (not Alpine) so lgpio C extension compiles correctly
FROM node:20-bookworm-slim

WORKDIR /app

# Install Python3 + build tools + hardware libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    i2c-tools \
    && rm -rf /var/lib/apt/lists/*

# Copy package files first for layer caching
COPY package.json package-lock.json* ./

# Install Node.js dependencies
RUN npm install --production

# Install Python dependencies (two stages to avoid index conflicts)
COPY hardware/requirements.txt ./hardware/requirements.txt
RUN python3 -m venv --system-site-packages /app/hardware/venv && \
    /app/hardware/venv/bin/pip install --no-cache-dir \
    "lgpio" \
    "spidev" \
    "aiohttp>=3.9.0" \
    "python-socketio[asyncio_client]>=5.11.0" \
    "aiohttp-cors>=0.7.0" \
    "pyserial>=3.5" \
    "opencv-python-headless>=4.8.0" \
    "numpy>=1.24.0"

# PyTorch (CPU-only) — use --extra-index-url so PyPI stays available as fallback
# timeout 600s for large wheel on slow networks (146MB on aarch64)
RUN /app/hardware/venv/bin/pip install --no-cache-dir --timeout 600 --retries 5 \
    "torch>=2.0.0" --extra-index-url https://download.pytorch.org/whl/cpu

# Ultralytics YOLO for road sign detection
RUN /app/hardware/venv/bin/pip install --no-cache-dir --timeout 600 --retries 5 \
    "ultralytics>=8.0.0" && \
    /app/hardware/venv/bin/pip install --no-cache-dir --force-reinstall \
    "opencv-python-headless>=4.8.0"

# Copy U-Net model weights for line-following
COPY line_following/best_unet.pth ./line_following/best_unet.pth

# Copy YOLO model weights for road sign detection
COPY detect-road-sign/best_trans.pt ./detect-road-sign/best_trans.pt

# Copy all source code
COPY . .

# Expose ports (5000=Node API, 8765=MJPEG camera stream)
EXPOSE 5000 8765

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD node -e "require('http').get('http://localhost:5000/health', (r) => { process.exit(r.statusCode === 200 ? 0 : 1) })"

CMD ["node", "server.js"]
