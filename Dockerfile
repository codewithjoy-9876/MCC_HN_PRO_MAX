FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    TZ=UTC

# System dependencies for MCC binary + supervisor
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    ca-certificates \
    curl \
    unzip \
    libicu74 \
    libssl3 \
    procps \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Download MCC binary at build time (kept out of git for size)
RUN chmod +x /app/download_mcc.sh /app/run_forever.sh /app/start_bot.sh || true \
    && /app/download_mcc.sh

EXPOSE 8080

CMD ["/app/run_forever.sh"]
