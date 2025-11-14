# syntax=docker/dockerfile:1.7

############################
#    BUILDER STAGE         #
############################
FROM python:3.12-slim AS builder

WORKDIR /app

# Install git only in the builder (needed for Git dependencies)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Copy dependency files (only these to maximize caching)
COPY pyproject.toml uv.lock ./

# Install dependencies using uv with full caching
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


############################
#    FINAL STAGE           #
############################
FROM python:3.12-slim

WORKDIR /app

# Copy the environment created by uv from builder
COPY --from=builder /app/.venv .venv

# Copy source code
COPY . .

# Use .venv Python
CMD [".venv/bin/python", "main.py"]
