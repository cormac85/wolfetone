FROM python:3.14-slim

WORKDIR /app

# Install RTL-SDR drivers/tools and clean up apt cache
RUN apt-get update && \
    apt-get install -y rtl-sdr && \
    rm -rf /var/lib/apt/lists/*

# uv installs the locked dependencies from pyproject.toml / uv.lock
COPY --from=ghcr.io/astral-sh/uv:0.5.9 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

ENV PATH="/app/.venv/bin:$PATH"

COPY climote_decode.py .

# Use -u to force unbuffered stdout so prints show up immediately in docker logs
CMD ["python3", "-u", "climote_decode.py"]