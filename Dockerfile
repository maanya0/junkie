# syntax=docker/dockerfile:1.7

########## BUILDER ##########
FROM python:3.12-slim AS builder
WORKDIR /app

# Install git for requirements.txt Git URLs
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Copy requirements file
COPY requirements.txt .

# Install everything into a local environment using uv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system -r requirements.txt

# Copy the project source
COPY . .

########## FINAL IMAGE ##########
FROM python:3.12-slim
WORKDIR /app

# Copy the installed Python environment
COPY --from=builder /usr/local /usr/local

# Copy the app code
COPY . .

CMD ["python", "main.py"]
