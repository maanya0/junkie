# Task 003: Small Fixes Sprint

**Status**: 🔨 In Progress  
**Created**: 2026-03-02

---

## Strategy

Each PR touches **different files** to avoid merge conflicts. PRs can be reviewed and merged independently.

---

## PR Queue

### PR A: `core/database.py` - Error Handling Improvements
**Branch**: `fix/database-error-handling`  
**Files**: `core/database.py` only

| Issue | Line | Fix |
|-------|------|-----|
| Empty POSTGRES_URL | L31 | Add guard + clear error message |
| delete_message swallows errors | L169-179 | Return boolean status |
| Access control writes unhandled | L411-486 | Add try/except with logging |

---

### PR B: `tools/history_tools.py` - Input Validation
**Branch**: `fix/history-tools-validation`  
**Files**: `tools/history_tools.py` only

| Issue | Line | Fix |
|-------|------|-----|
| No limit validation | L15-38 | Clamp to min=1, max=10000 |
| DB fallback unguarded | L36-38 | Add try/except |

---

### PR C: `agent/agent_factory.py` - Config & Cleanup
**Branch**: `fix/agent-factory-cleanup`  
**Files**: `agent/agent_factory.py`, `core/config.py`

| Issue | Line | Fix |
|-------|------|-----|
| Hardcoded timezone | Multiple | Move to AGENT_TIMEZONE config |
| Hardcoded Groq URL | L110,153,253 | Move to GROQ_BASE_URL config |
| Empty messages guard | L140-145 | Add length check |
| Unused imports | L16-18 | Remove WikipediaTools, SleepTools, YouTubeTools |

---

### PR D: `discord_bot/message_sync.py` - Logging & Safety
**Branch**: `fix/message-sync-logging`  
**Files**: `discord_bot/message_sync.py` only

| Issue | Line | Fix |
|-------|------|-----|
| Delete loop unguarded | L48-51 | Per-message try/except |
| Missing exc_info | L85-86 | Add exc_info=True |
| Missing channel name | L85-86 | Include channel name in log |

---

### PR E: `discord_bot/backfill.py` - Error Context
**Branch**: `fix/backfill-error-handling`  
**Files**: `discord_bot/backfill.py` only

| Issue | Line | Fix |
|-------|------|-----|
| Initial fetch unwrapped | L59-63 | Add try/except with context |
| Better error logging | Various | Add step-specific error messages |

---

## Progress

- [x] PR #16: database error handling - https://github.com/maanya0/junkie/pull/16
- [x] PR #13: history tools validation - https://github.com/maanya0/junkie/pull/13
- [ ] PR C: agent factory cleanup (deferred - larger scope)
- [x] PR #14: message sync logging - https://github.com/maanya0/junkie/pull/14
- [x] PR #15: backfill error handling - https://github.com/maanya0/junkie/pull/15

---

## Execution Order

1. Start all branches from `team`
2. Each branch modifies only its designated files
3. Create PRs targeting `team`
4. PRs can be merged in any order (no conflicts)
