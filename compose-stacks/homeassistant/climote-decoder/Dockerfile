FROM python:3.14-slim

WORKDIR /app

# Install RTL-SDR drivers/tools and clean up apt cache
RUN apt-get update && \
    apt-get install -y rtl-sdr && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY climote_decode.py .

# Use -u to force unbuffered stdout so prints show up immediately in docker logs
CMD ["python3", "-u", "climote_decode.py"]