from agno.tools import Toolkit
from agno.tools.function import ToolResult
from agno.media import Image

import logging
import discord
from typing import Optional, Union

logger = logging.getLogger(__name__)

class BioTools(Toolkit):
    def __init__(self, client=None):
        super().__init__(name="bio_tools")
        self.client = client
        self.register(self.get_user_details)
        self.register(self.get_user_avatar)

    def _get_discord_client(self, channel=None) -> Optional[discord.Client]:
        """
        Get Discord client instance with priority order:
        1. self.client (injected via constructor)
        2. Channel state (works for both guild and DM channels)
        3. Guild state (only for guild channels)
        
        Args:
            channel: Optional channel object to extract client from
            
        Returns:
            Discord client instance or None if not found
        """
        # Priority 1: Use injected client
        if self.client:
            return self.client
        
        # Priority 2: Get from channel state (works for DMs too)
        if channel:
            try:
                if hasattr(channel, '_state') and hasattr(channel._state, '_get_client'):
                    client = channel._state._get_client()
                    if client:
                        return client
            except Exception as e:
                logger.debug(f"[BioTools] Could not get client from channel state: {e}")
        
        # Priority 3: Get from guild (only for guild channels)
        if channel:
            guild = getattr(channel, 'guild', None)
            if guild:
                try:
                    if hasattr(guild, '_state') and hasattr(guild._state, '_get_client'):
                        client = guild._state._get_client()
                        if client:
                            return client
                except Exception as e:
                    logger.debug(f"[BioTools] Could not get client from guild state: {e}")
        
        return None

    async def _fetch_user(self, user_id: int, channel=None) -> Optional[Union[discord.User, discord.Member]]:
        """
        Fetch a Discord user by ID, handling both guild and DM contexts.
        
        Args:
            user_id: Discord user ID to fetch
            channel: Optional channel object for context
            
        Returns:
            User or Member object, or None if not found
        """
        client = self._get_discord_client(channel)
        if not client:
            logger.warning("[BioTools] Cannot fetch user: no Discord client available")
            return None
        
        guild = getattr(channel, 'guild', None) if channel else None
        is_dm = channel and (isinstance(channel, discord.DMChannel) or guild is None)
        
        # For guild channels, try to get member first (includes roles, status, etc.)
        if guild and not is_dm:
            try:
                # Try cache first
                member = guild.get_member(user_id)
                if member:
                    return member
                
                # Try fetching from API
                member = await guild.fetch_member(user_id)
                if member:
                    return member
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                logger.error(f"[BioTools] Error fetching member from guild: {e}")
        
        # Fallback: fetch user globally (works for both guild and DM contexts)
        try:
            user = await client.fetch_user(user_id)
            return user
        except discord.NotFound:
            logger.debug(f"[BioTools] User {user_id} not found")
            return None
        except discord.HTTPException as e:
            logger.error(f"[BioTools] Error fetching user: {e}")
            return None

    async def get_user_details(self, agent, user_id: int) -> str:
        """
        Fetches details for a Discord user by their ID.
        Works in both guild channels and DM channels.
        
        Args:
            agent (Agent): The agent instance.
            user_id (int): The Discord user ID to fetch details for.
            
        Returns:
            str: A formatted string containing user details (username, display name, avatar URL, etc.), or an error message.
        """
        channel_id = agent.session_state.get("channel_id")
        if not channel_id:
             return "Error: No channel context found in session state."

        # Fetch channel using injected client
        channel = None
        if self.client:
             channel = self.client.get_channel(int(channel_id))
        
        if not channel:
            return "Error: No execution context found. Cannot access Discord client."
        
        try:
            client = self._get_discord_client(channel)
            if not client:
                context_type = "DM" if (isinstance(channel, discord.DMChannel) or getattr(channel, 'guild', None) is None) else "guild"
                return f"Error: Cannot access Discord client in {context_type} context. User details unavailable."
            
            guild = getattr(channel, 'guild', None) if channel else None
            is_dm = channel and (isinstance(channel, discord.DMChannel) or guild is None)
            
            # Fetch user using helper method
            user = await self._fetch_user(user_id, channel)
            if not user:
                context_info = f"Guild: {guild.name}" if guild else "DM channel"
                return f"User with ID {user_id} not found in the current context ({context_info})."
            
            # Build basic user details (available for both User and Member)
            details = [
                f"User Details for ID: {user.id}",
                f"Username: {user.name}",
                f"Display Name: {user.display_name}",
                f"Bot: {user.bot}",
                f"Created At: {user.created_at}",
                f"Avatar URL: {user.avatar.url if user.avatar else user.default_avatar.url}",
            ]
            
            # Add guild-specific details only if user is a Member (guild context)
            if isinstance(user, discord.Member):
                details.append(f"Joined Server: {user.joined_at}")
                if user.nick:
                    details.append(f"Server Nickname: {user.nick}")
                
                roles = [r.name for r in user.roles if r.name != "@everyone"]
                if roles:
                    details.append(f"Roles: {', '.join(roles)}")
                
                # Status information (only available for Member objects)
                details.append(f"Status: {str(user.status)}")
                if user.mobile_status != discord.Status.offline:
                    details.append("Mobile Status: Online")
                if user.desktop_status != discord.Status.offline:
                    details.append("Desktop Status: Online")
                if user.web_status != discord.Status.offline:
                    details.append("Web Status: Online")
                
                # Activities & Custom Status
                if user.activities:
                    activity_list = []
                    for activity in user.activities:
                        if isinstance(activity, discord.CustomActivity):
                            activity_list.append(f"Custom Status: {activity.name} {f'({activity.emoji})' if activity.emoji else ''}")
                        elif isinstance(activity, discord.Spotify):
                            activity_list.append(f"Listening to Spotify: {activity.title} by {activity.artist}")
                        elif isinstance(activity, discord.Game):
                            activity_list.append(f"Playing: {activity.name}")
                        elif isinstance(activity, discord.Streaming):
                            activity_list.append(f"Streaming: {activity.name} ({activity.url})")
                        else:
                            activity_list.append(f"Activity: {activity.name}")
                    if activity_list:
                        details.append("Activities:\n  - " + "\n  - ".join(activity_list))
            
            # Fetch full user profile for Bio/Banner (requires API call, works for both contexts)
            try:
                # Always fetch full user profile to get banner, accent color, and bio
                # This works for both guild and DM contexts
                full_user = await client.fetch_user(user_id)
                if full_user:
                    if full_user.banner:
                        details.append(f"Banner URL: {full_user.banner.url}")
                    if full_user.accent_color:
                        details.append(f"Accent Color: {full_user.accent_color}")
                    # Check for bio (available in discord.py v2.0+)
                    if hasattr(full_user, 'bio') and full_user.bio:
                        details.append(f"Bio: {full_user.bio}")
                    elif hasattr(full_user, 'about_me') and full_user.about_me:
                        details.append(f"Bio: {full_user.about_me}")
            except Exception as e:
                logger.warning(f"[BioTools] Could not fetch full user profile: {e}")
            
            return "\n".join(details)
        
        except Exception as e:
            logger.error(f"[BioTools] Error getting user details: {e}", exc_info=True)
            return f"Error fetching user details: {str(e)}"
    async def get_user_avatar(self, agent, user_id: int) -> ToolResult:
        """
        Fetches the avatar of a Discord user by their ID and returns it as an image for analysis.
        Works in both guild channels and DM channels.
        
        Args:
            agent (Agent): The agent instance.
            user_id (int): The Discord user ID to fetch the avatar for.
            
        Returns:
            ToolResult: Contains the user's avatar image if found, or an error message.
        """
        channel_id = agent.session_state.get("channel_id")
        if not channel_id:
             return ToolResult(content="Error: No channel context found in session state.")

        # Fetch channel using injected client
        channel = None
        if self.client:
             channel = self.client.get_channel(int(channel_id))
             
        if not channel:
            return ToolResult(content="Error: No execution context found. Cannot access Discord client.")
        
        try:
            client = self._get_discord_client(channel)
            if not client:
                context_type = "DM" if (isinstance(channel, discord.DMChannel) or getattr(channel, 'guild', None) is None) else "guild"
                return ToolResult(content=f"Error: Cannot access Discord client in {context_type} context. Avatar unavailable.")
            
            # Fetch user using helper method
            user = await self._fetch_user(user_id, channel)
            if not user:
                guild = getattr(channel, 'guild', None) if channel else None
                context_info = f"Guild: {guild.name}" if guild else "DM channel"
                return ToolResult(content=f"User with ID {user_id} not found in the current context ({context_info}).")
            
            avatar_url = user.avatar.url if user.avatar else user.default_avatar.url
            
            # Create Image object
            image = Image(
                url=avatar_url,
                id=f"avatar_{user_id}",
                original_prompt=f"Avatar of user {user.name} ({user_id})"
            )
            
            return ToolResult(
                content=f"Here is the avatar for user {user.name} ({user_id})",
                images=[image]
            )
        
        except Exception as e:
            logger.error(f"[BioTools] Error getting user avatar: {e}", exc_info=True)
            return ToolResult(content=f"Error fetching user avatar: {str(e)}")
