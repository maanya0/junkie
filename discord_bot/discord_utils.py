import re
import logging

def resolve_mentions(message):
    """
    Replace <@12345> mentions with human-readable '@Name(12345)' for the model.
    """
    content = message.content
    for user in message.mentions:
        content = content.replace(f"<@{user.id}>", f"@{user.display_name}({user.id})")
    return content

def restore_mentions(response, guild):
    """
    Convert '@Name(12345)' back to real Discord mentions '<@12345>'.
    Only converts when @ symbol is present.
    Handles variations like '@Name(ID)', '@Name (ID)', etc.
    """
    # Pattern: Matches ONLY @Name(ID) format (requires @ symbol)
    # Captures the name and the ID
    pattern = r"@([^\(\)<>]+?)\s*\((\d+)\)"
    
    def repl(match):
        user_id = match.group(2)
        return f"<@{user_id}>"
    
    # Apply pattern to replace all instances
    response = re.sub(pattern, repl, response)
    return response

def correct_mentions(prompt, response):
    """
    Finds user IDs in the prompt and replaces plain @Name mentions in the response with <@ID>.
    In case of duplicate display names, it prioritizes the MOST RECENT user (last occurrence in prompt).
    """
    # Extract name-id pairs from prompt in order (oldest to newest).
    # Matches "Name(ID)" or "@Name(ID)" patterns common in the context.
    # We do NOT use set() here to preserve order.
    matches = re.findall(r"@?([^\(\)<>\n]+?)\s*\((\d+)\)", prompt)
    
    # Create mapping - later occurrences (more recent) overwrite earlier ones
    name_to_id = {name.strip(): uid for name, uid in matches if name.strip()}
    
    # Sort by name length descending to prevent partial matches
    sorted_names = sorted(name_to_id.keys(), key=len, reverse=True)
    
    logger = logging.getLogger(__name__)
    if sorted_names:
        logger.info(f"[correct_mentions] Found {len(sorted_names)} names in prompt: {sorted_names}")
    
    for name in sorted_names:
        uid = name_to_id[name]
        # Regex to match @Name not followed by (ID)
        # We use \b for word boundary, but we also need to handle cases where punctuation is immediately after
        # like @Name! or @Name? or @Name.
        # The negative lookahead (?!\s*\() ensures we don't replace if it's already in Name(ID) format
        
        # Escaped name for regex
        esc_name = re.escape(name)
        
        # Pattern:
        # @?       - Optional @ prefix (we want to match "Name" or "@Name")
        # {esc_name} - The name itself
        # (?!\s*\() - Negative lookahead: NOT followed by optional space and opening paren (ID)
        # (?=[^a-zA-Z0-9_]|$) - Positive lookahead: Followed by non-word char or end of string (ensures we don't match partial names like "Rob" in "Robert")
        
        pattern = re.compile(rf"@?{esc_name}(?!\s*\()(?=[^a-zA-Z0-9_]|$)", re.IGNORECASE)
        
        # Check if we have matches before replacing (for logging)
        if pattern.search(response):
            logger.info(f"[correct_mentions] Replacing '{name}' with '<@{uid}>'")
            # Replace with <@ID>
            response = pattern.sub(f"<@{uid}>", response)
        
    return response
