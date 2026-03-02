# Task 001: Context Management Improvements

**Status**: 📋 Planning  
**Priority**: High  
**Created**: 2026-03-02  
**Branch**: `feature/context-improvements` (to be created)

---

## Objective

Improve user experience and reduce hallucinations caused by improper context management in the Junkie Discord bot.

---

## Problem Analysis

### Current Architecture

```
Discord Message → append_message_to_cache() → PostgreSQL
                         ↓
                  build_context_prompt()
                         ↓
                  get_recent_context() → DB fetch + optional API backfill
                         ↓
                  Format messages with relative timestamps
                         ↓
                  Team.arun(input=prompt, ...)
```

### Identified Issues

#### 🔴 Critical (Causes Hallucinations)

| Issue | Description | Impact |
|-------|-------------|--------|
| **Future message leak** | `get_recent_context()` ignores `before_message` in DB fetch, potentially including messages sent AFTER the current one | Model sees "future" context, leading to temporal confusion |
| **Inconsistent content storage** | `append_message_to_cache()` stores `clean_content` (no attachments), but `update_message_in_cache()` stores with attachments | Edited messages have different format than original |
| **Incomplete history marker missing** | When DB has fewer messages than requested and backfill fails, no indicator is added | Model assumes it has full context when it doesn't |
| ~~**Session ID is channel-based**~~ | ~~`session_id=str(message.channel.id)`~~ | **INTENDED**: Group chatbot - shared context is correct |

#### 🟠 High (Degrades UX)

| Issue | Description | Impact |
|-------|-------------|--------|
| **Attachment-only messages invisible** | API fetch uses `m.clean_content` which can be empty for attachment-only messages | Model misses image/file context |
| **Reply context duplication** | Reply context appended separately, may duplicate message already in history | Redundant tokens, potential confusion |
| **Silent reply fetch failures** | If fetching replied-to message fails, bot proceeds without warning user | User gets confusing response |
| **Empty message handling inconsistent** | `append_message_to_cache()` skips empty messages, but API fetch stores `[Empty message]` | Gaps in conversation flow |

#### 🟡 Medium (Minor Issues)

| Issue | Description | Impact |
|-------|-------------|--------|
| **pytz dependency** | Timezone features silently disabled without pytz | Timestamps may be UTC when user expects IST |
| **Transient failure marks channel complete** | If API returns nothing due to error, `mark_channel_fully_backfilled(True)` prevents future attempts | Permanent incomplete history |
| **Non-image attachments ignored** | PDFs, text files, etc. are silently dropped | User doesn't know bot can't see their file |

---

## Root Cause Analysis

### 1. Temporal Confusion

**Problem**: The system prompt promises:
> "All messages in the conversation history include timestamps showing when they were sent"
> "Messages are in chronological order (oldest to newest)"
> "The LAST message in the conversation is the CURRENT message"

**Reality**: 
- DB fetch doesn't filter by `before_message`, so newer messages can appear
- If messages are edited/deleted during prompt building, ordering can shift
- No explicit marker distinguishing "history" from "current message"

**Fix Strategy**: 
- Add `before_id` filter to `get_messages()` DB query
- Add clear `--- CURRENT MESSAGE ---` delimiter in prompt
- Include message count and completeness indicator

### 2. Content Format Inconsistency

**Problem**: Three different storage paths produce different content:

| Path | Content Stored |
|------|----------------|
| `append_message_to_cache()` | `message.clean_content` (no attachments) |
| `update_message_in_cache()` | `content + attachments + embeds` |
| `fetch_and_cache_from_api()` | `content_parts` (attachments + embeds) but returns `m.clean_content` |

**Fix Strategy**:
- Standardize on a single format: `clean_content + [N attachments]` summary
- Update all storage paths to use the same formatter
- Add attachment metadata to formatted output when relevant

### 3. Session Scope (NOT AN ISSUE)

**Design Intent**: This is a **group chatbot** where all users in a channel share context.

**Current Behavior** (`session_id=str(channel.id)`): ✅ **CORRECT**
- All users see shared conversation history
- Bot can reference what any user said
- Memory is channel-scoped (appropriate for group context)

**No change needed** - shared sessions are intentional.

---

## Proposed Changes

### Phase 1: Fix Critical Issues (PR #1)

#### 1.1 Add `before_id` filter to DB queries

```python
# core/database.py
async def get_messages(channel_id: int, limit: int = 2000, before_id: int = None) -> List[Dict]:
    query = """
        SELECT message_id, channel_id, author_id, author_name, content, created_at, timestamp_str
        FROM messages
        WHERE channel_id = $1
    """
    if before_id:
        query += " AND message_id < $2"
        query += " ORDER BY created_at DESC LIMIT $3"
        params = [channel_id, before_id, limit]
    else:
        query += " ORDER BY created_at DESC LIMIT $2"
        params = [channel_id, limit]
    # ...
```

#### 1.2 Standardize content formatting

```python
# discord_bot/context_cache.py
def format_message_content(message) -> str:
    """Unified message content formatter."""
    content = message.clean_content or ""
    
    # Add attachment indicators
    if message.attachments:
        att_summary = ", ".join(
            f"[{a.content_type or 'file'}: {a.filename}]" 
            for a in message.attachments
        )
        content = f"{content} {att_summary}".strip()
    
    # Add embed indicators
    if message.embeds:
        content = f"{content} [+{len(message.embeds)} embed(s)]".strip()
    
    return content or "[Empty message]"
```

#### 1.3 Add context completeness indicator

```python
# discord_bot/context_cache.py
async def build_context_prompt(...) -> str:
    # ...
    
    completeness = "complete" if len(history) >= limit else f"partial ({len(history)}/{limit} available)"
    
    prompt_parts = [
        f"Channel: #{channel_name} ({channel_id}) in {guild_name}",
        f"Current Time: {now_str} (IST)",
        f"Context: {completeness}",
        "",
        "--- CONVERSATION HISTORY ---",
        "\n".join(history),
        "",
        "--- CURRENT MESSAGE ---",
        f"{current_msg_timestamp} {author_name}({author_id}): {raw_prompt}"
    ]
```

#### 1.4 ~~Fix session isolation~~ (NO CHANGE NEEDED)

**Group chatbot design**: Shared channel sessions are intentional.
- `session_id=str(message.channel.id)` is correct
- All users should share context in group conversations
- Bot can reference "what Alice said earlier" when Bob asks

### Phase 2: UX Improvements (PR #2)

#### 2.1 Deduplicate reply context

```python
# discord_bot/context_cache.py
async def build_context_prompt(..., reply_to_message=None) -> str:
    history = await get_recent_context(...)
    
    # Check if reply target is already in history
    reply_in_history = False
    if reply_to_message:
        reply_id = str(reply_to_message.id)
        reply_in_history = any(reply_id in line for line in history)
    
    # Only add reply context block if not already present
    if reply_to_message and not reply_in_history:
        prompt_parts.append(f"[REPLY CONTEXT: {format_reply(reply_to_message)}]")
```

#### 2.2 Warn on reply fetch failure

```python
# discord_bot/chat_handler.py
if message.reference and message.reference.message_id:
    try:
        reply_to_message = await message.channel.fetch_message(message.reference.message_id)
    except Exception as e:
        logger.warning(f"Failed to fetch reply context: {e}")
        # Add indicator to prompt
        raw_prompt = f"[Note: Reply context unavailable] {raw_prompt}"
```

#### 2.3 Notify user about unsupported attachments

```python
# discord_bot/chat_handler.py
unsupported = [a for a in message.attachments if not a.content_type or not a.content_type.startswith('image/')]
if unsupported:
    await message.add_reaction('📎')  # Indicate attachment acknowledged but not processed
```

### Phase 3: Edge Case Handling (PR #3)

- Make pytz a required dependency or use `zoneinfo` (Python 3.9+)
- Add retry logic for transient API failures before marking channel complete
- Consistent empty message handling across all paths

---

## Testing Strategy

### Unit Tests

```python
# tests/test_context_cache.py

async def test_before_id_filter():
    """Ensure messages after before_id are excluded."""
    
async def test_content_format_consistency():
    """Verify all storage paths produce same format."""
    
async def test_completeness_indicator():
    """Check partial context is marked correctly."""
```

### Integration Tests

1. Multi-user channel conversation - verify no session bleed
2. Reply to old message - verify context includes reply target
3. Attachment-only message - verify visibility in history
4. Rapid message editing - verify latest content shown

### Manual Testing Scenarios

| Scenario | Expected Behavior |
|----------|-------------------|
| Ask "what did I just say?" | Bot refers to user's previous message, not future ones |
| User A asks "what did B say?" | Bot correctly references B's messages in shared context |
| Reply to message from 2 hours ago | Bot acknowledges the reply context correctly |
| Send image with no text | Bot acknowledges image in context |
| Group discussion with 5 users | Bot tracks all participants correctly |

---

## Implementation Plan

### PR #1: Critical Fixes (Est: 4-6 hours)
- [ ] Add `before_id` to `get_messages()` in `core/database.py`
- [ ] Update `get_recent_context()` to pass `before_message.id`
- [ ] Create `format_message_content()` helper
- [ ] Apply formatter to all storage paths
- [ ] Add `--- CURRENT MESSAGE ---` delimiter
- [ ] Add context completeness indicator
- [ ] Write unit tests

### PR #2: UX Improvements (Est: 2-3 hours)
- [ ] Deduplicate reply context
- [ ] Add reply fetch failure warning
- [ ] Add unsupported attachment notification
- [ ] Response chunking improvements (remove prefix spam)

### PR #3: Edge Cases (Est: 2 hours)
- [ ] Require pytz or use zoneinfo
- [ ] Add retry logic for API failures
- [ ] Consistent empty message handling

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Temporal confusion reports | Unknown | 0 |
| "Wrong context" user complaints | Unknown | -80% |
| Multi-user attribution accuracy | Unknown | 100% |
| Attachment visibility | Partial | Full |

---

## Files to Modify

| File | Changes |
|------|--------|
| `core/database.py` | Add `before_id` filter |
| `discord_bot/context_cache.py` | Format consistency, completeness indicator, dedup |
| `discord_bot/chat_handler.py` | Session ID fix, reply warning, attachment notification |
| `requirements.txt` | Add pytz as required (or document Python 3.9+ for zoneinfo) |

---

## Open Questions

1. ~~**Session scope**~~: **RESOLVED** - Per-channel is correct for group chatbot design.
   
2. **Attachment handling**: Should we extract text from PDFs/documents?
   - Would require additional dependencies (PyPDF2, etc.)
   - Could delegate to code-agent for processing

3. **Context limit**: Is `TEAM_LEADER_CONTEXT_LIMIT` appropriately sized?
   - Need to balance context richness vs token usage

---

## References

- `discord_bot/context_cache.py` - Main context building logic
- `discord_bot/chat_handler.py` - Message handling and session management
- `core/database.py` - Database operations
- `agent/system_prompt.md` - Expected context format documentation
