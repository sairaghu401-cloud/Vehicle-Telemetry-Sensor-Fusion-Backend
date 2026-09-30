# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# System deps needed to build psycopg2 (Postgres driver) from source wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first — Docker caches layers, so if requirements.txt
# hasn't changed, this pip install layer is reused instead of re-run on every
# code change. This is the single biggest thing that makes rebuilds fast.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
