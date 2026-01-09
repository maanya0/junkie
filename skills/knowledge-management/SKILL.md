---
name: knowledge-management
description: Managing the bot's knowledge base - adding, searching, and filtering content
---

# Knowledge Management Skill

## When to Use
- User asks to "remember" or "save" information
- User uploads documents/attachments to be stored
- User wants to add URLs or web pages to knowledge
- Questions require searching stored knowledge
- User asks about previously stored content

## Adding Content

### For Discord Attachments
Use `add_attachment` with:
- `url`: The Discord CDN URL
- `filename`: Original file name
- `metadata`: Include `{topic, user_id, type}` for better filtering

### For Web Content
Use `add_url` with:
- `url`: Full URL to the webpage or PDF
- `metadata`: Include `{source, topic, type}`

## Searching Content

The knowledge base is automatically searched when `search_knowledge=True`.
For specific filtering, use metadata:
- Filter by user: `{user_id: "123"}`
- Filter by topic: `{topic: "recipes"}`
- Filter by type: `{type: "documentation"}`

## Best Practices
1. Always include relevant metadata when adding content
2. Use descriptive topics for better retrieval
3. Confirm with user after successful addition
4. Search knowledge before making claims about stored content
