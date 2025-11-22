import os
from dotenv import load_dotenv

load_dotenv()

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
USE_REDIS = os.getenv("USE_REDIS", "false").lower() == "true"

# Postgres Configuration
POSTGRES_URL = os.getenv("POSTGRES_URL", "")

# Model and Provider Configuration
PROVIDER = os.getenv("CUSTOM_PROVIDER", "groq")  # default provider
MODEL_NAME = os.getenv("CUSTOM_MODEL", "openai/gpt-oss-120b")
SUPERMEMORY_KEY = os.getenv("SUPERMEMORY_API_KEY")
CUSTOM_PROVIDER_API_KEY = os.getenv("CUSTOM_PROVIDER_API_KEY", None)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Agent Configuration
MODEL_TEMPERATURE = float(os.getenv("MODEL_TEMPERATURE", "0.3"))
MODEL_TOP_P = float(os.getenv("MODEL_TOP_P", "0.9"))
AGENT_HISTORY_RUNS = int(os.getenv("AGENT_HISTORY_RUNS", "1"))
AGENT_RETRIES = int(os.getenv("AGENT_RETRIES", "2"))
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
DEBUG_LEVEL = int(os.getenv("DEBUG_LEVEL", "1"))
MAX_AGENTS = int(os.getenv("MAX_AGENTS", "100"))

# Tracing Configuration
TRACING_ENABLED = os.getenv("TRACING", "false").lower() == "true"
PHOENIX_API_KEY = os.getenv("PHOENIX_API_KEY")
PHOENIX_ENDPOINT = os.getenv(
    "PHOENIX_ENDPOINT",
    "https://app.phoenix.arize.com/s/maanyapatel145/v1/traces"
)
PHOENIX_PROJECT_NAME = os.getenv("PHOENIX_PROJECT_NAME", "junkie")

# MCP Configuration
MCP_URLS = os.getenv("MCP_URLS", "").strip()

# Chat Context Agent Configuration
CONTEXT_AGENT_MODEL = os.getenv("CONTEXT_AGENT_MODEL", "gemini-2.5-flash-lite")
CONTEXT_AGENT_MAX_MESSAGES = int(os.getenv("CONTEXT_AGENT_MAX_MESSAGES", "80000"))
TEAM_LEADER_CONTEXT_LIMIT = int(os.getenv("TEAM_LEADER_CONTEXT_LIMIT", "100"))
