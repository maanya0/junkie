# chat_handler.py
import asyncio
import inspect
import logging
import os
import sys
import time
from typing import Optional

import discord
from agno.media import Image

from agent.agent_factory import create_team_for_user, get_or_create_team
from core.config import BOT_OWNER_ID, DEFAULT_USER_FILTER_MODE, TEAM_LEADER_CONTEXT_LIMIT
from core.database import (
    add_access_control_user,
    add_admin_user,
    close_db,
    get_access_control_mode,
    get_access_control_users,
    get_admin_users,
    init_db,
    remove_access_control_user,
    remove_admin_user,
    set_access_control_mode,
)
from core.execution_context import set_current_channel, set_current_channel_id
from discord_bot.backfill import start_backfill_task
from discord_bot.context_cache import (
    append_message_to_cache,
    build_context_prompt,
    delete_message_from_cache,
    update_message_in_cache,
)
from discord_bot.discord_utils import correct_mentions, resolve_mentions, restore_mentions
from tools.tools_factory import get_mcp_tools, setup_mcp

logger = logging.getLogger(__name__)

VALID_FILTER_MODES = {"whitelist", "blacklist"}


async def async_ask_junkie(
    user_text: str,
    user_id: str,
    session_id: str,
    images: list = None,
    client=None,
) -> str:
    """
    Run a per-user Team to generate a response for the given prompt and return the generated text.
    
    Parameters:
        user_text (str): The prompt or message text to process.
        user_id (str): Identifier for the user invoking the Team; used to obtain or create the per-user Team.
        session_id (str): Session identifier to correlate conversation context.
        images (list, optional): List of image attachments (e.g., Image objects) to include as part of the input context.
        client (optional): Optional client or SDK instance passed through when creating or retrieving the Team.
    
    Returns:
        str: The Team-generated response text; if the Team returns no usable content, an apology message asking the user to rephrase is returned.
    
    Raises:
        Exception: Propagates any exception raised while obtaining or running the Team after logging it.
    """
    team = await get_or_create_team(user_id, client=client)
    try:
        result = await team.arun(
            input=user_text,
            user_id=user_id,
            session_id=session_id,
            images=images,
        )

        content = result.content if result and hasattr(result, "content") else ""
        if not content or not content.strip():
            return "I apologize, but I couldn't generate a valid response. Please try rephrasing your question."

        return content
    except Exception as e:
        logger.error(f"Team error for user {user_id}: {e}", exc_info=True)
        raise


def _normalize_mode(mode: Optional[str]) -> str:
    """
    Normalize an access-control mode name into a valid filter mode.
    
    Parameters:
        mode (Optional[str]): Candidate mode string (may be None or empty).
    
    Returns:
        "whitelist" or "blacklist" — the validated mode; defaults to "whitelist" when the input is missing or not one of the valid modes.
    """
    normalized = (mode or "").strip().lower()
    return normalized if normalized in VALID_FILTER_MODES else "whitelist"


def setup_chat(bot):
    """
    Configure chat behavior and register access-control commands and event handlers on the given bot.
    
    This function wires up runtime access-control state (whitelist/blacklist, admin and master users), command handlers for managing that state (status, mode/toggle, add/remove/list for access and admins, help), lifecycle handlers for startup and shutdown (including DB initialization, MCP setup, backfill and post-backfill message sync), and message events for caching, command dispatch, and a chatbot invocation (prompt construction, reply generation, mention/image handling, and chunked replies). It mutates in-memory access state and schedules background tasks as part of bot setup.
    
    Parameters:
        bot: The bot instance to configure; event handlers and commands will be registered on this object.
    """
    owner_id = BOT_OWNER_ID.strip() if BOT_OWNER_ID else ""
    access_state = {
        "mode": _normalize_mode(DEFAULT_USER_FILTER_MODE),
        "ids": set(),
        "admins": set(),
    }
    backfill_task: Optional[asyncio.Task] = None

    def _is_master_user(user_id: int) -> bool:
        """
        Determine whether the given user ID is the configured master (bot owner).
        
        If a configured owner ID exists, the function checks against that value; otherwise it checks whether the ID matches the currently connected bot user's ID.
        
        Parameters:
            user_id (int): Discord user ID to check.
        
        Returns:
            `true` if the user is the master/owner, `false` otherwise.
        """
        if owner_id:
            return str(user_id) == owner_id
        return bot.bot.user is not None and user_id == bot.bot.user.id

    def _is_admin_user(user_id: int) -> bool:
        """
        Determine whether the given user ID has administrative privileges for the bot.
        
        Parameters:
            user_id (int): Discord user ID to check.
        
        Returns:
            `true` if the user is an administrator or the configured master user, `false` otherwise.
        """
        normalized = str(user_id)
        return _is_master_user(user_id) or normalized in access_state["admins"]

    def _is_authorized_user(user_id: int) -> bool:
        """
        Check whether a user is permitted to use chat features under the current access control settings.
        
        Master users and admins are always permitted. In "blacklist" mode, other users are permitted unless their ID is listed; in "whitelist" mode, other users are permitted only if their ID is listed.
        
        Parameters:
            user_id (int): Discord user ID to evaluate.
        
        Returns:
            `true` if the user is authorized, `false` otherwise.
        """
        if _is_admin_user(user_id):
            return True

        normalized = str(user_id)
        if access_state["mode"] == "blacklist":
            return normalized not in access_state["ids"]
        return normalized in access_state["ids"]

    async def _reload_access_state():
        """
        Reload the in-memory access_state from persistent storage.
        
        Fetches the configured access-control mode, the list of allowed/blocked user IDs, and the list of admin user IDs from the database, normalizes the mode, and updates access_state['mode'], access_state['ids'], and access_state['admins'] accordingly.
        """
        db_mode = await get_access_control_mode(default_mode=_normalize_mode(DEFAULT_USER_FILTER_MODE))
        access_state["mode"] = _normalize_mode(db_mode)
        access_state["ids"] = await get_access_control_users()
        access_state["admins"] = await get_admin_users()

    def _access_summary() -> str:
        """
        Provides a compact summary of the current access-control state.
        
        The string includes the active mode, the count of configured access IDs, the count of admin IDs, and the master status ('configured' if an owner_id is set, otherwise 'fallback:self user').
        
        Returns:
            str: Summary string in the form "mode=<mode>, access_ids=<count>, admins=<count>, master=<configured|fallback:self user>".
        """
        return (
            f"mode={access_state['mode']}, access_ids={len(access_state['ids'])}, "
            f"admins={len(access_state['admins'])}, master={'configured' if owner_id else 'fallback:self user'}"
        )

    @bot.command("accessstatus")
    async def accessstatus(ctx):
        """
        Send the current access-control summary to the invoking channel when the message author is an admin.
        
        If the author is not an admin, the command does nothing (no message is sent).
        """
        if not _is_admin_user(ctx.author.id):
            return
        await ctx.send(f"Access control status: {_access_summary()}")

    @bot.command("accessmode")
    async def accessmode(ctx, mode: str = ""):
        """
        Set or display the bot's access-control mode via an admin command.
        
        If called without a mode argument, sends the current access-control mode to the channel.
        If a mode is provided, validates that it is either "whitelist" or "blacklist", updates the stored access-control mode, updates in-memory state, and sends a confirmation message. Invocation by non-admin users is ignored.
        
        Parameters:
            ctx: The command invocation context (message and channel information).
            mode (str): Desired access mode; expected values are "whitelist" or "blacklist". If empty, the current mode is shown.
        """
        if not _is_admin_user(ctx.author.id):
            return

        if not mode:
            await ctx.send(f"Current mode: `{access_state['mode']}`")
            return

        normalized = _normalize_mode(mode)
        if normalized != mode.strip().lower():
            await ctx.send("Invalid mode. Use `whitelist` or `blacklist`.")
            return

        await set_access_control_mode(normalized)
        access_state["mode"] = normalized
        await ctx.send(f"Access control mode set to `{normalized}`.")

    @bot.command("accesstoggle")
    async def accesstoggle(ctx):
        """
        Toggle the configured access-control mode between "whitelist" and "blacklist" and notify the command context.
        
        If the invoking user is an admin, updates the persistent access-control mode and the in-memory state, then sends a confirmation message to the provided command context. If the user is not an admin, the command is ignored.
        
        Parameters:
            ctx: The command invocation context used to send the confirmation message.
        """
        if not _is_admin_user(ctx.author.id):
            return

        next_mode = "blacklist" if access_state["mode"] == "whitelist" else "whitelist"
        await set_access_control_mode(next_mode)
        access_state["mode"] = next_mode
        await ctx.send(f"Access control mode toggled to `{next_mode}`.")

    @bot.command("accessadd")
    async def accessadd(ctx, user_id: str = "", *, note: str = None):
        """
        Add a Discord user ID to the bot's access-control list.
        
        Validates that `user_id` contains only digits, persists the new access entry to the database (with an optional `note` and the command invoker as the adder), updates the in-memory access_state, and sends a confirmation message to the invoking context. If `user_id` is not numeric, sends a usage hint. The command is a no-op if the invoker is not an admin.
        
        Parameters:
            ctx: The command context used to send responses and identify the invoker.
            user_id (str): The Discord user ID to add.
            note (str, optional): An optional note stored with the access entry.
        """
        if not _is_admin_user(ctx.author.id):
            return

        if not user_id.isdigit():
            await ctx.send("Usage: `.accessadd <discord_user_id> [note]`")
            return

        await add_access_control_user(int(user_id), added_by=ctx.author.id, note=note)
        access_state["ids"].add(user_id)
        await ctx.send(f"Added `{user_id}` to access-control IDs.")

    @bot.command("accessremove")
    async def accessremove(ctx, user_id: str = ""):
        """
        Remove a user ID from the bot's access-control list.
        
        This admin-only command validates that `user_id` is a numeric Discord user ID, prevents removing the configured master/owner override, removes the ID from persistent access-control storage, updates the in-memory access set, and sends a confirmation or usage message to the invoking context.
        
        Parameters:
            user_id (str): The Discord user ID to remove from access control; expected as a string of digits.
        """
        if not _is_admin_user(ctx.author.id):
            return

        if not user_id.isdigit():
            await ctx.send("Usage: `.accessremove <discord_user_id>`")
            return

        if owner_id and user_id == owner_id:
            await ctx.send("Cannot remove the master user override.")
            return

        await remove_access_control_user(int(user_id))
        access_state["ids"].discard(user_id)
        await ctx.send(f"Removed `{user_id}` from access-control IDs.")

    @bot.command("accesslist")
    async def accesslist(ctx):
        """
        List configured access-control user IDs to the invoking context's channel.
        
        If the caller is not an admin, the command takes no action. If no IDs are configured, sends a notice stating that. Otherwise sends a message containing the total count and up to the first 100 configured IDs, comma-separated.
        """
        if not _is_admin_user(ctx.author.id):
            return

        ids = sorted(access_state["ids"])
        if not ids:
            await ctx.send("No access-control IDs configured.")
            return

        joined = ", ".join(ids[:100])
        await ctx.send(f"Configured access-control IDs ({len(ids)}): {joined}")

    @bot.command("adminadd")
    async def adminadd(ctx, user_id: str = ""):
        """
        Add a Discord user ID to the bot's admin list and confirm the change to the invoking context.
        
        If the invoking user is not the configured master, no action is taken. If the provided user_id is not a numeric Discord ID, a usage message is sent. If the user_id matches the configured master/owner override, a notice is sent and no change is made. Otherwise the user_id is recorded as an admin (database update and in-memory state) and a confirmation message is sent to the context.
        
        Parameters:
            ctx: The command invocation context; used to send response messages and to identify the caller.
            user_id (str): The Discord user ID to add as an admin; must be a string of digits.
        """
        if not _is_master_user(ctx.author.id):
            return

        if not user_id.isdigit():
            await ctx.send("Usage: `.adminadd <discord_user_id>`")
            return

        if owner_id and user_id == owner_id:
            await ctx.send("Master user is already an admin by override.")
            return

        await add_admin_user(int(user_id), added_by=ctx.author.id)
        access_state["admins"].add(user_id)
        await ctx.send(f"Added `{user_id}` as bot admin.")

    @bot.command("adminremove")
    async def adminremove(ctx, user_id: str = ""):
        """
        Remove a user from the bot's admin list.
        
        Attempts to remove the Discord user with the given user_id from persistent admin storage and the in-memory admin set, and sends a confirmation or usage message to the invoking context. If a master/owner override is configured, that user cannot be removed.
        
        Parameters:
            ctx: The command invocation context used to send feedback to the caller.
            user_id (str): Discord user ID string of the admin to remove; must be numeric.
        """
        if not _is_master_user(ctx.author.id):
            return

        if not user_id.isdigit():
            await ctx.send("Usage: `.adminremove <discord_user_id>`")
            return

        if owner_id and user_id == owner_id:
            await ctx.send("Cannot remove master user override.")
            return

        await remove_admin_user(int(user_id))
        access_state["admins"].discard(user_id)
        await ctx.send(f"Removed `{user_id}` from bot admins.")

    @bot.command("adminlist")
    async def adminlist(ctx):
        """
        List configured admin user IDs and send the result to the invoking context.
        
        If the caller is not an admin this function does nothing. When an owner/master is configured, that ID is placed first in the list. Sends a summary message with up to the first 100 admin IDs, or a notice if no admins are configured.
        """
        if not _is_admin_user(ctx.author.id):
            return

        admins = sorted(access_state["admins"])
        if owner_id:
            admins = [owner_id] + [admin for admin in admins if admin != owner_id]

        if not admins:
            await ctx.send("No admins configured in DB. Only master override is active.")
            return

        await ctx.send(f"Admins ({len(admins)} incl. master): {', '.join(admins[:100])}")

    @bot.command("accesshelp")
    async def accesshelp(ctx):
        """
        Send a concise help message listing access-control and admin commands to the invoking channel.
        
        If the command author is not an admin, the function returns without sending anything.
        
        Parameters:
            ctx (discord.ext.commands.Context): The command invocation context.
        """
        if not _is_admin_user(ctx.author.id):
            return

        await ctx.send(
            "Access commands: `.accessstatus`, `.accessmode <whitelist|blacklist>`, `.accesstoggle`, "
            "`.accessadd <id> [note]`, `.accessremove <id>`, `.accesslist`, `.adminlist`. "
            "Master-only: `.adminadd <id>`, `.adminremove <id>`."
        )

    @bot.event
    async def on_ready():
        """
        Perform startup initialization when the Discord client becomes ready.
        
        Initializes MCP tools, the database, and in-memory access state; computes the set of text and private channels to backfill; and schedules a background task that runs channel backfill followed by a post-backfill message synchronization (uses MESSAGE_SYNC_LIMIT environment variable to limit messages per channel). Logs startup progress and warns if the configured BOT_OWNER_ID (master override) is missing.
        """
        logger.info("[on_ready] Bot ready event triggered!")

        if not owner_id:
            logger.warning(
                "[on_ready] BOT_OWNER_ID is not configured. Falling back to self user id for master override."
            )

        await setup_mcp()

        logger.info("[on_ready] Initializing database...")
        await init_db()
        await _reload_access_state()
        logger.info("[on_ready] Database initialized | %s", _access_summary())

        text_channels = [
            c
            for c in bot.bot.get_all_channels()
            if isinstance(c, (discord.TextChannel, discord.DMChannel, discord.GroupChannel))
        ]
        for c in bot.bot.private_channels:
            if c not in text_channels:
                text_channels.append(c)

        logger.info(f"[on_ready] Found {len(text_channels)} channels to backfill")

        async def run_backfill_and_sync():
            """
            Run the backfill task and then perform a post-backfill message synchronization for the configured text channels.
            
            This starts the background backfill, waits for it to finish, then calls the message sync for the most recent messages. The number of messages synced is taken from the MESSAGE_SYNC_LIMIT environment variable (default 200). Progress and errors are logged; exceptions from the backfill/sync sequence are caught and logged.
            """
            try:
                logger.info("[on_ready] Starting backfill task...")
                await start_backfill_task(text_channels)
                logger.info("[on_ready] Backfill task completed")

                from discord_bot.message_sync import sync_all_channels

                sync_limit = int(os.getenv("MESSAGE_SYNC_LIMIT", "200"))
                logger.info(
                    f"[on_ready] Starting post-backfill message sync (last {sync_limit} messages)..."
                )
                await sync_all_channels(text_channels, sync_limit=sync_limit)
                logger.info("[on_ready] Message sync completed")

            except Exception as e:
                logger.error(f"[on_ready] Backfill/sync task failed: {e}", exc_info=True)

        nonlocal backfill_task
        if backfill_task and not backfill_task.done():
            logger.info("[on_ready] Backfill+sync task is already running; skipping duplicate startup")
            return

        logger.info(
            "[on_ready] Creating backfill+sync background task for %s channels...",
            len(text_channels),
        )
        backfill_task = asyncio.create_task(run_backfill_and_sync())
        logger.info("[on_ready] Backfill+sync task created - running in background")

    @bot.event
    async def on_disconnect():
        """Clean shutdown of database connections and resources."""
        nonlocal backfill_task
        if backfill_task and not backfill_task.done():
            logger.info("[on_disconnect] Cancelling backfill+sync background task...")
            backfill_task.cancel()
            try:
                await backfill_task
            except asyncio.CancelledError:
                logger.info("[on_disconnect] Backfill+sync background task cancelled")
            finally:
                backfill_task = None

        logger.info("[on_disconnect] Bot disconnecting, closing database pool...")
        await close_db()

    @bot.event
    async def on_message(message):
        """
        Handle an incoming Discord message: process bot commands or, when prefixed with "!", run the chatbot flow and reply.
        
        This function appends the message to the local cache, delegates messages starting with the bot command prefix to the command processor, and for messages starting with "!" verifies authorization, builds conversational context (including reply context and image attachments), invokes the chat backend to generate a response, and sends the response back to the channel in chunks. Errors during reply generation are logged and result in a short error message sent to the channel. The handler also updates current channel context and logs key events.
        
        Parameters:
            message: discord.Message
                The incoming Discord message to process.
        """
        await append_message_to_cache(message)

        if message.content.startswith(bot.prefix):
            await bot.bot.process_commands(message)
            return

        chatbot_prefix = "!"
        if message.content.startswith(chatbot_prefix):
            if not _is_authorized_user(message.author.id):
                logger.warning(
                    "[chatbot] Ignoring unauthorized user %s in channel %s (mode=%s)",
                    message.author.id,
                    message.channel.id,
                    access_state["mode"],
                )
                return

            processed_content = resolve_mentions(message)
            raw_prompt = processed_content[len(chatbot_prefix) :].strip()

            logger.info(
                f"[chatbot] Building context for channel {message.channel.id}, user {message.author.id}"
            )

            reply_to_message = None
            if message.reference and message.reference.resolved:
                if isinstance(message.reference.resolved, type(message)):
                    reply_to_message = message.reference.resolved
                    logger.info(f"[chatbot] Found reply context: {reply_to_message.id}")
            elif message.reference and message.reference.message_id:
                try:
                    reply_to_message = await message.channel.fetch_message(message.reference.message_id)
                    logger.info(f"[chatbot] Fetched reply context: {reply_to_message.id}")
                except Exception as e:
                    logger.warning(f"[chatbot] Failed to fetch reply context: {e}")

            prompt = await build_context_prompt(
                message,
                raw_prompt,
                limit=TEAM_LEADER_CONTEXT_LIMIT,
                reply_to_message=reply_to_message,
            )
            logger.info(f"[chatbot] Context prompt built, length: {len(prompt)} characters")

            # Process attachments - images go to model, others get noted
            # Image extensions to check when content_type is missing
            IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.svg')
            
            def _is_image_attachment(att) -> bool:
                """Check if attachment is an image by content_type or file extension."""
                # Check content_type first
                if att.content_type and att.content_type.startswith("image/"):
                    return True
                # Fallback: check filename extension
                if att.filename and att.filename.lower().endswith(IMAGE_EXTENSIONS):
                    return True
                # Fallback: check URL extension
                if att.url:
                    url_lower = att.url.lower().split('?')[0]  # Remove query params
                    if url_lower.endswith(IMAGE_EXTENSIONS):
                        return True
                return False
            
            images = []
            non_image_attachments = []
            
            if message.attachments:
                for attachment in message.attachments:
                    if _is_image_attachment(attachment):
                        images.append(Image(url=attachment.url))
                        logger.info("[chatbot] Found image attachment: %s", attachment.url)
                    else:
                        # Track non-image attachments (PDFs, documents, etc.)
                        non_image_attachments.append(attachment)
                        logger.info("[chatbot] Non-image attachment: %s (%s)", attachment.filename, attachment.content_type)

            if reply_to_message and reply_to_message.attachments:
                for attachment in reply_to_message.attachments:
                    if _is_image_attachment(attachment):
                        images.append(Image(url=attachment.url))
                        logger.info("[chatbot] Found reply image attachment: %s", attachment.url)
            
            # Add reaction to acknowledge non-image attachments (user feedback)
            if non_image_attachments:
                try:
                    await message.add_reaction('\U0001F4CE')  # 📎 paperclip emoji
                    logger.info("[chatbot] Added paperclip reaction for %d non-image attachment(s)", len(non_image_attachments))
                except (discord.Forbidden, discord.HTTPException, discord.NotFound) as e:
                    logger.warning("[chatbot] Failed to add attachment reaction: %s", e)

            if not raw_prompt and not message.attachments:
                return

            async with message.channel.typing():
                user_id = str(message.author.id)
                session_id = str(message.channel.id)

                channel_name = getattr(message.channel, "name", "DM")
                logger.info(
                    f"[chatbot] Agent invoked in channel {channel_name} ({message.channel.id}) by user {message.author.name} ({user_id})"
                )

                set_current_channel_id(message.channel.id)
                set_current_channel(message.channel)

                start_time = time.time()
                try:
                    reply = await async_ask_junkie(
                        prompt,
                        user_id=user_id,
                        session_id=session_id,
                        images=images,
                        client=bot.bot,
                    )
                except Exception as e:
                    logger.exception(f"[chatbot] Failed to generate reply for user {user_id}")
                    await message.channel.send(
                        "An internal error occurred while processing your request. The team has been notified."
                    )
                    return

                end_time = time.time()
                time_taken = end_time - start_time

            final_reply = restore_mentions(reply, message.guild)
            final_reply = final_reply.replace("**🗿 hero:**", "")
            final_reply = correct_mentions(prompt, final_reply)
            final_reply += f"\n\n*(Time taken: {time_taken:.2f}s)*"

            chunk_size = 1900
            for chunk in [final_reply[i : i + chunk_size] for i in range(0, len(final_reply), chunk_size)]:
                await message.channel.send(f"**🗿 hero:**\n{chunk}")

    @bot.event
    async def on_message_edit(before, after):
        """Handle message edits to update cache."""
        await update_message_in_cache(before, after)

    @bot.event
    async def on_message_delete(message):
        """Handle message deletions to update cache."""
        await delete_message_from_cache(message)


async def main_cli():
    """
    Start the CLI entry point: initialize MCP tools and, if running in an interactive terminal, create a per-user Team and run its CLI application.
    
    If the process is not attached to a TTY, the CLI run is skipped and a message is printed. Ensures MCP tools are closed on exit; errors raised while closing are suppressed.
    """
    await setup_mcp()
    try:
        if sys.stdin and sys.stdin.isatty():
            _, cli_team = create_team_for_user("cli_user")
            if hasattr(cli_team, "acli_app"):
                await cli_team.acli_app()
            else:
                print("CLI app not available for Team object.")
        else:
            print("Non-interactive environment detected; skipping CLI app.")
    finally:
        mcp = get_mcp_tools()
        if mcp:
            try:
                close_call = getattr(mcp, "close", None)
                if close_call:
                    maybe_result = close_call()
                    if inspect.isawaitable(maybe_result):
                        await maybe_result
            except Exception:
                pass
