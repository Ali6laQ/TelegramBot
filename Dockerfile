FROM node:22-bookworm-slim AS node_bin

FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install FFmpeg (for high-quality muxing & thumbnails) and runtime deps for Node.js
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libatomic1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy Node.js 22 binary for yt-dlp YouTube EJS challenge solving (works on amd64 & arm64)
COPY --from=node_bin /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt uvloop

COPY bot ./bot
COPY run.py .

RUN mkdir -p /app/downloads /app/data

CMD ["python", "run.py"]
