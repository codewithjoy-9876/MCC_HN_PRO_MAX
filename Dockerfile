# HN_PRO_MAX production Dockerfile (Railway-ready)
# Downloads the MCC binary at build time so the GitHub repo stays small.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
    PORT=8080 \
    TZ=UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        ca-certificates \
        procps \
        tzdata \
        curl \
        wget \
        unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Fetch the official Minecraft Console Client binary (Linux x64, latest stable)
# Reference: https://mccteam.github.io/guide/installation.html
RUN curl -fsSL https://mccteam.github.io/install.sh | sh \
    && ls -lh /app/MinecraftClient \
    && chmod +x /app/MinecraftClient

# Copy supervisor + config + helpers
COPY . /app

RUN chmod +x /app/run_forever.sh /app/start_bot.sh /app/download_mcc.sh /app/install_mcc.sh || true \
    && /app/download_mcc.sh \
    && chmod +x /app/MinecraftClient || true \
    && python3 -m py_compile /app/mcc_supervisor.py /app/health_server.py

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8080}/health" || exit 1

CMD ["/app/run_forever.sh"]
