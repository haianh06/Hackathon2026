# Object Detection Service - Python
# Lightweight container for custom object detection algorithms
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install minimal system deps for OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY detect-object/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code
COPY detect-object/ ./detect-object/

ENV PYTHONUNBUFFERED=1

EXPOSE 9002

CMD ["python3", "detect-object/detector_service.py"]
