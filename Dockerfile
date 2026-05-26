# HN_PRO_MAX production Dockerfile (Railway-ready)
# Prefer the bundled MCC binary from the repository; download only if it's missing.
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

# Copy supervisor + config + helpers + bundled MCC binary (if present in repo)
COPY . /app

RUN chmod +x /app/run_forever.sh /app/start_bot.sh /app/download_mcc.sh /app/install_mcc.sh /app/resolve_runtime_config.py || true \
    && if [ ! -x /app/MinecraftClient ]; then /app/download_mcc.sh; else echo "Using bundled MinecraftClient from repository"; fi \
    && chmod +x /app/MinecraftClient || true \
    && ls -lh /app/MinecraftClient \
    && python3 -m py_compile /app/mcc_supervisor.py /app/health_server.py /app/resolve_runtime_config.py

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8080}/health" || exit 1

CMD ["/app/run_forever.sh"]
