from agno.tools import Toolkit
from agno.tools.function import ToolResult
from agno.media import Image
from core.execution_context import get_current_channel
import logging
import discord

logger = logging.getLogger(__name__)

class BioTools(Toolkit):
    def __init__(self, client=None):
        super().__init__(name="bio_tools")
        self.client = client
        self.register(self.get_user_details)
        self.register(self.get_user_avatar)

    async def get_user_details(self, user_id: int) -> str:
        """
        Fetches details for a Discord user by their ID.
        
        Args:
            user_id (int): The Discord user ID to fetch details for.
            
        Returns:
            str: A formatted string containing user details (username, display name, avatar URL, etc.), or an error message.
        """
        channel = get_current_channel()
        if not channel:
            return "Error: No execution context found. Cannot access Discord client."
            
        try:
            # We need the client or guild to fetch the user/member
            # channel.guild might be available if it's a guild channel
            guild = getattr(channel, 'guild', None)
            client = getattr(channel, '_state', None) and getattr(channel._state, '_get_client', lambda: None)()
            
            # If we can't get client from channel state (internal API), try to rely on guild
            if not client:
                 if self.client:
                     client = self.client
                 elif guild:
                     if hasattr(guild, '_state') and hasattr(guild._state, '_get_client'):
                         client = guild._state._get_client()
                     elif hasattr(guild, 'me') and hasattr(guild.me, '_state') and hasattr(guild.me._state, '_get_client'):
                         client = guild.me._state._get_client()
                 # Try channel state directly (works for DMs too)
                 elif hasattr(channel, '_state') and hasattr(channel._state, '_get_client'):
                     client = channel._state._get_client()
            
            if not client:
                logger.warning("[BioTools] Could not access Discord client instance.")

            # Actually, the best way if we have a channel is usually channel.guild.get_member(user_id)
            # or await channel.guild.fetch_member(user_id)
            
            member = None
            if guild:
                # Try to get from cache first
                member = guild.get_member(user_id)
                if not member:
                    try:
                        member = await guild.fetch_member(user_id)
                    except discord.NotFound:
                        pass
                    except discord.HTTPException as e:
                        logger.error(f"[BioTools] Error fetching member: {e}")
            
            # If not found in guild (or DM), try fetching user globally if we have client access
            # But we might not have easy access to client instance here without passing it down.
            # However, `channel` objects usually are attached to the client.
            
            user = member
            
            # If not found in guild (or DM), try fetching user globally if we have client access
            if not user and client:
                try:
                    user = await client.fetch_user(user_id)
                except discord.NotFound:
                    pass
                except discord.HTTPException as e:
                    logger.error(f"[BioTools] Error fetching user: {e}")

            if not user:
                 return f"User with ID {user_id} not found in the current context (Guild: {guild.name if guild else 'None'})."

            details = [
                f"User Details for ID: {user.id}",
                f"Username: {user.name}",
                f"Display Name: {user.display_name}",
                f"Bot: {user.bot}",
                f"Created At: {user.created_at}",
                f"Avatar URL: {user.avatar.url if user.avatar else user.default_avatar.url}",
            ]
            
            if isinstance(user, discord.Member):
                 details.append(f"Joined Server: {user.joined_at}")
                 if user.nick:
                     details.append(f"Server Nickname: {user.nick}")
                 roles = [r.name for r in user.roles if r.name != "@everyone"]
                 if roles:
                     details.append(f"Roles: {', '.join(roles)}")
                 
                 # Status
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

            # Fetch full user profile for Bio/Banner (requires API call)
            try:
                # We need a client instance to call fetch_user. 
                # If we are in a guild context, we might not have direct client access easily unless we hack it.
                # But wait, we can try to use the member object if it has a way, or use the client we found earlier.
                
                # If we found a client earlier:
                full_user = None
                if client:
                    full_user = await client.fetch_user(user_id)
                elif guild:
                     # guild.fetch_member doesn't give bio. We need client.fetch_user
                     # Try to get client from guild
                     if hasattr(guild, '_state') and hasattr(guild._state, '_get_client'):
                         client = guild._state._get_client()
                         if client:
                             full_user = await client.fetch_user(user_id)
                
                if full_user:
                    if full_user.banner:
                        details.append(f"Banner URL: {full_user.banner.url}")
                    if full_user.accent_color:
                        details.append(f"Accent Color: {full_user.accent_color}")
                    # Note: discord.py v2.0+ supports user.bio on fetch_user result (it's actually 'about_me' or just not always available depending on version/intents)
                    # Checking attributes safely
                    if hasattr(full_user, 'bio') and full_user.bio:
                        details.append(f"Bio: {full_user.bio}")
                    
            except Exception as e:
                logger.warning(f"[BioTools] Could not fetch full user profile: {e}")

            return "\n".join(details)

        except Exception as e:
            logger.error(f"[BioTools] Error getting user details: {e}", exc_info=True)
            return f"Error fetching user details: {str(e)}"
    async def get_user_avatar(self, user_id: int) -> ToolResult:
        """
        Fetches the avatar of a Discord user by their ID and returns it as an image for analysis.
        
        Args:
            user_id (int): The Discord user ID to fetch the avatar for.
            
        Returns:
            ToolResult: Contains the user's avatar image if found, or an error message.
        """
        channel = get_current_channel()
        if not channel:
            return ToolResult(content="Error: No execution context found. Cannot access Discord client.")
            
        try:
            # We need the client or guild to fetch the user/member
            guild = getattr(channel, 'guild', None)
            client = getattr(channel, '_state', None) and getattr(channel._state, '_get_client', lambda: None)()
            
            # If we can't get client from channel state (internal API), try to rely on guild
            if not client:
                 if self.client:
                     client = self.client
                 elif guild:
                     if hasattr(guild, '_state') and hasattr(guild._state, '_get_client'):
                         client = guild._state._get_client()
                     elif hasattr(guild, 'me') and hasattr(guild.me, '_state') and hasattr(guild.me._state, '_get_client'):
                         client = guild.me._state._get_client()
                 # Try channel state directly (works for DMs too)
                 elif hasattr(channel, '_state') and hasattr(channel._state, '_get_client'):
                     client = channel._state._get_client()

            if not client:
                logger.warning("[BioTools] Could not access Discord client instance.")

            member = None
            if guild:
                # Try to get from cache first
                member = guild.get_member(user_id)
                if not member:
                    try:
                        member = await guild.fetch_member(user_id)
                    except discord.NotFound:
                        pass
                    except discord.HTTPException as e:
                        logger.error(f"[BioTools] Error fetching member: {e}")
            
            user = member
            
            # If not found in guild, try fetching user globally if we have client access
            if not user and client:
                try:
                    user = await client.fetch_user(user_id)
                except discord.NotFound:
                    pass
                except discord.HTTPException as e:
                    logger.error(f"[BioTools] Error fetching user: {e}")

            if not user:
                 return ToolResult(content=f"User with ID {user_id} not found.")

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
