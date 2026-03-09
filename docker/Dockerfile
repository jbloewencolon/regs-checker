FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY pyproject.toml ./
RUN pip install --upgrade pip setuptools
RUN pip install --no-cache-dir --no-build-isolation ".[dev]"

# Application code
COPY . .

# Dagster home
RUN mkdir -p /opt/dagster/dagster_home
COPY docker/dagster.yaml /opt/dagster/dagster_home/dagster.yaml

EXPOSE 8000 3000

CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
