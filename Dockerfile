# ── Stage 1: Builder ─────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime ─────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        fonts-liberation \
        libnss3 \
        libxss1 \
        libasound2 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy installed packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app
COPY . .

# Ensure start script has executable permissions
RUN chmod +x start.sh

# Render binds to $PORT dynamically
EXPOSE 8000
CMD ["./start.sh"]
