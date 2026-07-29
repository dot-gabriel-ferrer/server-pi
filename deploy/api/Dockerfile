FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN pip install --upgrade pip && pip install .

EXPOSE 8000
CMD ["uvicorn", "server_pi.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
