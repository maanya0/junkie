# chat_handler.py
import asyncio
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
    """Run the user's Team with improved error handling and response validation."""
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
    normalized = (mode or "").strip().lower()
    return normalized if normalized in VALID_FILTER_MODES else "whitelist"


def setup_chat(bot):
    owner_id = BOT_OWNER_ID.strip() if BOT_OWNER_ID else ""
    access_state = {
        "mode": _normalize_mode(DEFAULT_USER_FILTER_MODE),
        "ids": set(),
        "admins": set(),
    }

    def _is_master_user(user_id: int) -> bool:
        if owner_id:
            return str(user_id) == owner_id
        return bot.bot.user is not None and user_id == bot.bot.user.id

    def _is_admin_user(user_id: int) -> bool:
        normalized = str(user_id)
        return _is_master_user(user_id) or normalized in access_state["admins"]

    def _is_authorized_user(user_id: int) -> bool:
        if _is_master_user(user_id):
            return True

        normalized = str(user_id)
        if access_state["mode"] == "blacklist":
            return normalized not in access_state["ids"]
        return normalized in access_state["ids"]

    async def _reload_access_state():
        db_mode = await get_access_control_mode(default_mode=_normalize_mode(DEFAULT_USER_FILTER_MODE))
        access_state["mode"] = _normalize_mode(db_mode)
        access_state["ids"] = await get_access_control_users()
        access_state["admins"] = await get_admin_users()

    def _access_summary() -> str:
        return (
            f"mode={access_state['mode']}, access_ids={len(access_state['ids'])}, "
            f"admins={len(access_state['admins'])}, master={'configured' if owner_id else 'fallback:self user'}"
        )

    @bot.command("accessstatus")
    async def accessstatus(ctx):
        if not _is_admin_user(ctx.author.id):
            return
        await ctx.send(f"Access control status: {_access_summary()}")

    @bot.command("accessmode")
    async def accessmode(ctx, mode: str = ""):
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
        if not _is_admin_user(ctx.author.id):
            return

        next_mode = "blacklist" if access_state["mode"] == "whitelist" else "whitelist"
        await set_access_control_mode(next_mode)
        access_state["mode"] = next_mode
        await ctx.send(f"Access control mode toggled to `{next_mode}`.")

    @bot.command("accessadd")
    async def accessadd(ctx, user_id: str = "", *, note: str = None):
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
        if not _is_admin_user(ctx.author.id):
            return

        await ctx.send(
            "Access commands: `.accessstatus`, `.accessmode <whitelist|blacklist>`, `.accesstoggle`, "
            "`.accessadd <id> [note]`, `.accessremove <id>`, `.accesslist`, `.adminlist`. "
            "Master-only: `.adminadd <id>`, `.adminremove <id>`."
        )

    @bot.event
    async def on_ready():
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

        logger.info(
            f"[on_ready] Creating backfill+sync background task for {len(text_channels)} channels..."
        )
        asyncio.create_task(run_backfill_and_sync())
        logger.info("[on_ready] Backfill+sync task created - running in background")

    @bot.event
    async def on_disconnect():
        """Clean shutdown of database connections and resources."""
        logger.info("[on_disconnect] Bot disconnecting, closing database pool...")
        await close_db()

    @bot.event
    async def on_message(message):
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
            if not raw_prompt:
                return

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

            images = []
            if message.attachments:
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith("image/"):
                        images.append(Image(url=attachment.url))
                        logger.info(f"[chatbot] Found image attachment: {attachment.url}")

            if reply_to_message and reply_to_message.attachments:
                for attachment in reply_to_message.attachments:
                    if attachment.content_type and attachment.content_type.startswith("image/"):
                        images.append(Image(url=attachment.url))
                        logger.info(f"[chatbot] Found reply image attachment: {attachment.url}")

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
                    await message.channel.send(f"**Error:** Failed to process request: {str(e)[:500]}")
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
    """CLI entrypoint — create a per-user Team and run its CLI app if available."""
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
                    if hasattr(close_call, "__await__"):
                        await close_call()
                    else:
                        close_call()
            except Exception:
                pass
