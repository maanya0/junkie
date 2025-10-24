# Use Python 3.12 slim for small size
FROM python:3.12-slim

# Avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Set working directory
WORKDIR /app

# If you have any pip packages from Git, keep git. Otherwise skip it.
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies efficiently
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Start the app
CMD ["python", "main.py"]
