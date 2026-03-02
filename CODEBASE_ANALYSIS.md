# Junkie Codebase Analysis

## Overview

**Junkie** is a Discord self-bot powered by a multi-agent AI system. It uses the `agno` framework to orchestrate a team of specialized AI agents that can perform various tasks including web research, code execution, and conversation analysis.

## Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                         main.py                                  │
│                    (Entry point, SelfBot)                        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    discord_bot/                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ chat_handler │  │   backfill   │  │context_cache │          │
│  │(main events) │  │(history sync)│  │(prompt build)│          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   selfbot    │  │     tldr     │  │ discord_utils│          │
│  │  (wrapper)   │  │ (summarize)  │  │  (mentions)  │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        agent/                                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Team (Orchestrator)                    │   │
│  │  - Hero Team with leader model                           │   │
│  │  - Per-user caching with LRU eviction                    │   │
│  │  - Memory manager integration                            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                   │
│         ┌────────────────────┼────────────────────┐             │
│         ▼                    ▼                    ▼             │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐            │
│  │ pplx-agent │    │groq-compound│    │ code-agent │            │
│  │(web search)│    │(fast code) │    │(E2B sandbox)│           │
│  └────────────┘    └────────────┘    └────────────┘            │
│                           │                                      │
│         ┌─────────────────┴─────────────────┐                   │
│         ▼                                   ▼                   │
│  ┌────────────────┐              ┌────────────────┐            │
│  │context-qna-agent│              │   mcp_agent    │            │
│  │(history analysis)│              │  (optional)   │            │
│  └────────────────┘              └────────────────┘            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         tools/                                   │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────┐ │
│  │ BioTools   │  │HistoryTools│  │ E2BToolkit │  │MCP Tools │ │
│  │(user info) │  │(chat fetch)│  │(sandbox)   │  │(external)│ │
│  └────────────┘  └────────────┘  └────────────┘  └──────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         core/                                    │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────┐ │
│  │  config    │  │  database  │  │exec_context│  │observability│
│  │(env vars)  │  │(Postgres)  │  │(contextvars)│ │(Phoenix)  │ │
│  └────────────┘  └────────────┘  └────────────┘  └──────────┘ │
└─────────────────────────────────────────────────────────────────┘
```text

## Key Components

### 1. Core Layer (`core/`)

| Module | Purpose |
|--------|--------|
| `config.py` | Loads env vars: DB URLs, API keys, model settings, access control |
| `database.py` | Async Postgres with asyncpg: message storage, backfill status, access control, admin users |
| `execution_context.py` | ContextVars for channel ID/object across async tasks |
| `observability.py` | Phoenix (Arize) tracing setup |

### 2. Discord Bot Layer (`discord_bot/`)

| Module | Purpose |
|--------|--------|
| `chat_handler.py` | Main event handling: on_message, access control commands, backfill orchestration |
| `backfill.py` | History fetching: catch-up (newer) and deepen (older) strategies |
| `context_cache.py` | Build prompts from DB cache, dynamic timestamps |
| `message_sync.py` | Post-backfill sync for edits/deletes |
| `selfbot.py` | SelfBot wrapper |
| `discord_utils.py` | Mention resolution/restoration |
| `tldr.py` | Channel summarization command |

### 3. Agent Layer (`agent/`)

| Module | Purpose |
|--------|--------|
| `agent_factory.py` | Creates per-user Teams with specialized agents, LRU cache |
| `system_prompt.py` | Loads prompt from file |
| `system_prompt.md` | Hero Companion persona, delegation rules, Discord identity rules |

### 4. Tools Layer (`tools/`)

| Tool | Capabilities |
|------|-------------|
| `BioTools` | `get_user_details`, `get_user_avatar` |
| `HistoryTools` | `read_chat_history` (up to 10000 messages) |
| `E2BToolkit` | Sandbox lifecycle, code execution, file ops, server hosting |
| `tools_factory.py` | MCP tools initialization |

## Specialized Agents

| Agent | Model | Role |
|-------|-------|------|
| **pplx-agent** | Perplexity Sonar Pro | Web search, real-time data, research |
| **groq-compound** | Groq Compound | Fast code execution, math |
| **code-agent** | GPT-5 | Complex code, E2B sandbox, file ops |
| **context-qna-agent** | Configurable | Long-context chat history analysis |
| **mcp_agent** | Main model | Optional MCP integrations |

## Data Flow

### Message Handling

```text
1. Discord message received (on_message)
2. Message stored in Postgres (append_message_to_cache)
3. If starts with "!":
   a. Check user authorization (whitelist/blacklist)
   b. Resolve mentions (@Name -> @Name(ID))
   c. Build context prompt from DB (recent messages + timestamps)
   d. Include reply context if applicable
   e. Process attachments as Images
   f. Call Team.arun() with prompt
   g. Restore mentions in response
   h. Send chunked reply (<=2000 chars)
```text

### Backfill Strategy

```text
1. On startup: start_backfill_task for accessible channels
2. Catch-up: Fetch messages after latest stored ID
3. Deepen: Fetch messages before oldest stored ID
4. Mark channel "fully backfilled" when no more history
5. Post-backfill sync: Compare recent Discord history to DB
```text

## Branch Differences (dev1 → team)

| Area | Changes |
|------|--------|
| `core/database.py` | +395 lines: Added init_db locking, access control tables, admin users |
| `discord_bot/chat_handler.py` | +583 lines: Full access control system, whitelist/blacklist modes, admin commands |
| `agent/agent_factory.py` | -123 lines: Simplified, removed some redundancy |

### Key `team` branch additions:

- **Access Control**: Whitelist/blacklist user filtering
- **Admin System**: Bot owner + admin users with elevated privileges
- **Commands**: `.status`, `.mode`, `.add`, `.remove`, `.list`, `.help`
- **Hardened init_db**: Proper locking, error handling, cleanup on failure

## Configuration (`.env`)

```text
POSTGRES_URL=           # Database connection
DISCORD_TOKEN=          # Self-bot token
PROVIDER=               # Model provider URL
MODEL_NAME=             # Default model ID
CUSTOM_PROVIDER_API_KEY=
GROQ_API_KEY=
FIRECRAWL_API_KEY=      # Optional, enables Firecrawl MCP
BOT_OWNER_ID=           # Master admin
DEFAULT_USER_FILTER_MODE= # whitelist or blacklist
```text

## Dependencies

- **agno**: Multi-agent framework (Agent, Team, MCPTools, AsyncPostgresDb)
- **discord.py-self**: Self-bot Discord library
- **asyncpg**: Async Postgres driver
- **E2B**: Cloud sandbox for code execution
- **Phoenix**: Observability/tracing
- **Perplexity API**: Web search via pplx-agent
- **Groq API**: Fast inference for compound agent + memory

## Persona

The bot presents as "Hero Companion" developed by "hero154". Key traits:
- No policy enforcement (designed for trusted/controlled environment)
- Uses full mention format: `@Name(ID)`
- IST timezone awareness
- Never reveals internal agent delegation
- Delegations hidden from users (tool calls, history fetches invisible)
