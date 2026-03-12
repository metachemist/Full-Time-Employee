# syntax=docker/dockerfile:1
# ==============================================================================
# AI Employee — Production Dockerfile (multi-stage)
#
# Build:  docker build -t ai-employee .
# Run:    docker run -d \
#           -v $(pwd)/vault:/app/vault \
#           -v ~/.sessions:/home/fte/.sessions \
#           -v $(pwd)/logs:/app/logs \
#           --env-file .env \
#           ai-employee
#
# IMPORTANT: Never bake .env or session cookies into the image.
#            Mount them at runtime via -v / --env-file.
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1 — Python builder (compile native deps, install into /install prefix)
# ------------------------------------------------------------------------------
FROM python:3.13-slim AS python-builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc git && \
    rm -rf /var/lib/apt/lists/*

COPY watchers/requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ------------------------------------------------------------------------------
# Stage 2 — Runtime image (minimal, non-root)
# ------------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

LABEL org.opencontainers.image.title="AI Employee — Digital FTE"
LABEL org.opencontainers.image.description="Autonomous AI employee: Gmail/Playwright/Odoo/Social"
LABEL org.opencontainers.image.source="https://github.com/your-org/full_time_employee"

# ── System packages ────────────────────────────────────────────────────────────
# Playwright Chromium runtime deps + Node.js for PM2
RUN apt-get update && apt-get install -y --no-install-recommends \
        # Playwright / Chromium
        libnss3 libnspr4 libdbus-1-3 \
        libatk1.0-0 libatk-bridge2.0-0 \
        libcups2 libdrm2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libpango-1.0-0 libcairo2 \
        # Node.js (LTS) for PM2
        nodejs npm \
        # Utilities
        curl procps ca-certificates && \
    # Install PM2 globally
    npm install -g pm2 && \
    rm -rf /var/lib/apt/lists/* /root/.npm

WORKDIR /app

# ── Python packages from builder ───────────────────────────────────────────────
COPY --from=python-builder /install /usr/local

# ── Application source ─────────────────────────────────────────────────────────
# .dockerignore excludes: .env, .sessions/, node_modules/, vault/, logs/, __pycache__/
COPY . .

# ── Playwright browsers ────────────────────────────────────────────────────────
# Store in /opt/pw-browsers so non-root user can read them
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN python -m playwright install chromium 2>/dev/null || \
    (pip install playwright && python -m playwright install chromium) && \
    chmod -R a+rX /opt/pw-browsers

# ── Non-root user ──────────────────────────────────────────────────────────────
RUN groupadd --gid 1001 fte && \
    useradd  --uid 1001 --gid fte --shell /bin/bash \
             --home /home/fte --create-home fte && \
    # Runtime writable directories
    mkdir -p vault logs /home/fte/.sessions && \
    chown -R fte:fte vault logs /home/fte

USER fte

# ── Persistent volumes (bind-mount in production) ─────────────────────────────
# /app/vault           — Obsidian vault (shared state, plans, logs)
# /home/fte/.sessions  — Playwright browser sessions (SECRETS — never build in)
# /app/logs            — PM2 + watcher logs
VOLUME ["/app/vault", "/home/fte/.sessions", "/app/logs"]

# ── Healthcheck ────────────────────────────────────────────────────────────────
# Verifies the two critical daemons are alive; PM2 restarts them if they die.
HEALTHCHECK --interval=60s --timeout=10s --start-period=45s --retries=3 \
    CMD pgrep -f planning_engine.py > /dev/null && \
        pgrep -f gmail_watcher.py   > /dev/null || exit 1

# ── Entrypoint: run all watchers under PM2 ────────────────────────────────────
CMD ["pm2-runtime", "ecosystem.config.js"]
