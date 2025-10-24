#Dockerfile

#use python 3.12 and install redis too

FROM python:3.12-slim

# Install redis server and git (required for pip to install from Git URLs)
RUN apt-get update && \
    apt-get install -y redis-server git && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install uv
RUN uv pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose Redis default port
EXPOSE 6379

# Default command (can be overridden)
# Start Redis and then the app
CMD ["bash", "-lc", "redis-server --daemonize yes && python main.py"]
