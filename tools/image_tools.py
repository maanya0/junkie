"""
Image Tools - Allow the team leader to view images from URLs in context.

Provides a minimal tool for the agent to fetch and analyze images from URLs
that appear in chat messages (attachments, embeds, etc.)
"""

from agno.tools import Toolkit
from agno.tools.function import ToolResult
from agno.media import Image

import logging
from typing import List

logger = logging.getLogger(__name__)

# Maximum number of images per tool call to prevent token overload
MAX_IMAGES_PER_CALL = 3


class ImageTools(Toolkit):
    """Tools for viewing images from URLs in the chat context."""
    
    def __init__(self):
        super().__init__(name="image_tools")
        self.register(self.view_images)

    async def view_images(
        self,
        agent,
        image_urls: List[str],
        context: str = ""
    ) -> ToolResult:
        """
        View and analyze images from URLs in the chat context.
        
        Use this when you need to see/analyze images that users have shared
        (attachments, embedded images, linked images, etc.)
        
        IMPORTANT: Limit to 3 images per call to prevent token overload.
        If more images are needed, make multiple calls focusing on the 
        most relevant ones first.
        
        Args:
            agent: The agent instance.
            image_urls: List of image URLs to view (max 3 per call).
                       Supports: Discord CDN, Tenor, Imgur, direct image links.
            context: Optional context about what you're looking for in the images.
        
        Returns:
            ToolResult: Contains the images for visual analysis and descriptions.
        """
        if not image_urls:
            return ToolResult(content="Error: No image URLs provided.")
        
        # Enforce limit to prevent token overload
        if len(image_urls) > MAX_IMAGES_PER_CALL:
            logger.warning(
                f"[ImageTools] Requested {len(image_urls)} images, limiting to {MAX_IMAGES_PER_CALL}"
            )
            image_urls = image_urls[:MAX_IMAGES_PER_CALL]
        
        images = []
        errors = []
        
        for i, url in enumerate(image_urls):
            try:
                # Validate URL looks like an image
                url_lower = url.lower()
                is_image_url = any(ext in url_lower for ext in [
                    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
                    'cdn.discordapp.com', 'media.discordapp.net',
                    'tenor.com', 'imgur.com', 'i.imgur.com',
                    'pbs.twimg.com', 'media.tenor.com'
                ])
                
                if not is_image_url and not url.startswith(('http://', 'https://')):
                    errors.append(f"URL {i+1}: Invalid URL format")
                    continue
                
                # Create Image object for the model
                image = Image(
                    url=url,
                    id=f"context_image_{i+1}",
                    original_prompt=context or f"Image {i+1} from chat context"
                )
                images.append(image)
                
            except Exception as e:
                logger.error(f"[ImageTools] Error processing URL {url}: {e}")
                errors.append(f"URL {i+1}: {str(e)}")
        
        if not images:
            error_msg = "Could not load any images. " + "; ".join(errors)
            return ToolResult(content=error_msg)
        
        # Build response
        content_parts = [f"Loaded {len(images)} image(s) for analysis."]
        if context:
            content_parts.append(f"Context: {context}")
        if errors:
            content_parts.append(f"Note: {len(errors)} URL(s) failed to load.")
        
        return ToolResult(
            content="\n".join(content_parts),
            images=images
        )
