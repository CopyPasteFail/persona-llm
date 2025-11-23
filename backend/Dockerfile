FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

WORKDIR /app

# System deps (minimal; add as needed for extra libs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl build-essential \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

EXPOSE 8080

# Real API entrypoint. Override to api.mock:app if you want mock mode.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
