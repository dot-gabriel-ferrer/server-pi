FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN apt-get update && apt-get install -y --no-install-recommends bluetooth bluez libglib2.0-0 ffmpeg wget \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --upgrade pip && pip install .

CMD ["python", "-m", "server_pi.ble_collector.main"]
