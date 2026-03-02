# Task 002: Attachment Handling Fixes

**Status**: 🔨 In Progress  
**Priority**: High  
**Created**: 2026-03-02  
**Branch**: `feature/attachment-handling`

---

## Problem Summary

Attachments are handled inconsistently across different code paths, causing:
1. **Attachment-only messages invisible** in context history
2. **Inconsistent storage** between different functions
3. **Non-image attachments silently ignored** by the model
4. **No user feedback** about unsupported attachments

---

## Current State Analysis

### Storage Paths Comparison

| Function | Location | Attachments Handling | Stored Format |
|----------|----------|---------------------|---------------|
| `append_message_to_cache()` | L315-333 | ❌ **IGNORED** (early return if empty) | `message.clean_content` |
| `update_message_in_cache()` | L343-365 | ✅ Appends `[Attachment: url]` | `content_parts` joined |
| `fetch_and_cache_from_api()` | L203-229 | ✅ Appends `[Attachment: url]` | `content_parts` joined |
| `build_context_prompt()` | L285-307 | ❌ Uses `clean_content` for replies | No storage |

### Image Processing in chat_handler.py (L545-556)

```python
# Only images with content_type are processed
if attachment.content_type and attachment.content_type.startswith("image/"):
    images.append(Image(url=attachment.url))
# Non-images: silently ignored
```

### Database Schema (core/database.py)

```sql
CREATE TABLE messages (
    content TEXT NOT NULL,  -- No separate attachment field
    ...
);
```

---

## Issues to Fix

### Issue 1: `append_message_to_cache()` ignores attachments

**Current behavior** (L315-333):
```python
async def append_message_to_cache(message):
    if not message.content.strip():  # ❌ Skips attachment-only messages!
        return
    await store_message(
        content=message.clean_content,  # ❌ No attachment info
        ...
    )
```

**Fix**: Include attachment markers even when text is empty.

### Issue 2: `build_context_prompt()` uses clean_content for replies

**Current behavior** (L285-307):
```python
reply_content = reply_to_message.clean_content  # ❌ Loses attachment info
```

**Fix**: Format reply content with attachment markers.

### Issue 3: Non-image attachments invisible to model

**Current behavior**: PDFs, documents, etc. are not mentioned anywhere.

**Fix**: Add attachment summary to context prompt.

### Issue 4: No user feedback for unsupported attachments

**Current behavior**: User sends PDF, bot ignores it silently.

**Fix**: Add reaction or note when attachments can't be processed.

---

## Implementation Plan

### Step 1: Create unified content formatter

```python
# discord_bot/context_cache.py
def format_message_content(message) -> str:
    """Unified message content formatter with attachment support."""
    content_parts = []
    
    # Text content
    text = message.clean_content if hasattr(message, 'clean_content') else message.get('content', '')
    if text and text.strip():
        content_parts.append(text.strip())
    
    # Attachments
    attachments = getattr(message, 'attachments', None) or message.get('attachments', [])
    for att in attachments:
        if hasattr(att, 'filename'):
            att_type = (att.content_type or 'file').split('/')[0]
            content_parts.append(f"[{att_type}: {att.filename}]")
        elif isinstance(att, dict):
            content_parts.append(f"[attachment: {att.get('filename', 'unknown')}]")
    
    # Embeds
    embeds = getattr(message, 'embeds', None) or message.get('embeds', [])
    if embeds and not attachments:
        content_parts.append(f"[{len(embeds)} embed(s)]")
    
    return ' '.join(content_parts) if content_parts else '[Empty message]'
```

### Step 2: Update `append_message_to_cache()`

```python
async def append_message_to_cache(message):
    content = format_message_content(message)
    if content == '[Empty message]' and not message.attachments:
        return  # Only skip truly empty messages
    
    await store_message(
        content=content,
        ...
    )
```

### Step 3: Update `build_context_prompt()` for reply attachments

```python
# Use formatter for reply content
reply_content = format_message_content(reply_to_message)
```

### Step 4: Add attachment summary to current message context

```python
# In chat_handler.py, add attachment info to prompt
attachment_summary = ""
if message.attachments:
    att_list = [f"{a.filename} ({a.content_type or 'unknown'})" for a in message.attachments]
    attachment_summary = f"\n[Attachments: {', '.join(att_list)}]"

raw_prompt = processed_content[len(chatbot_prefix):].strip() + attachment_summary
```

### Step 5: Add user feedback for non-image attachments

```python
# In chat_handler.py
non_image_attachments = [
    a for a in message.attachments 
    if not a.content_type or not a.content_type.startswith('image/')
]
if non_image_attachments:
    await message.add_reaction('📎')  # Acknowledge attachment
```

---

## Files to Modify

| File | Changes |
|------|--------|
| `discord_bot/context_cache.py` | Add `format_message_content()`, update storage functions |
| `discord_bot/chat_handler.py` | Add attachment summary to prompt, add reaction feedback |

---

## Progress Tracker

- [x] Analysis complete
- [x] Create `format_message_content()` helper
- [x] Update `append_message_to_cache()`
- [x] Update `fetch_and_cache_from_api()` to use formatter
- [x] Update `update_message_in_cache()` to use formatter  
- [x] Update `build_context_prompt()` for replies
- [x] Add reaction feedback for non-image attachments (📎)
- [ ] Test all paths
- [x] Create PR

---

## Testing Checklist

| Scenario | Expected Result |
|----------|----------------|
| Send image only (no text) | Message appears in history as `[image: filename.png]` |
| Send PDF attachment | Message shows `[application: document.pdf]` in history |
| Send text + image | Both text and `[image: ...]` appear |
| Reply to image-only message | Reply context includes attachment info |
| Edit message to add attachment | Updated content includes attachment |
| Send PDF to bot | Bot adds 📎 reaction, attachment noted in context |
