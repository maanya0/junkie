"""Knowledge tools for agent - Think/Search/Analyze + Discord ingestion."""
import os
import logging
import tempfile
import aiohttp
from agno.tools.knowledge import KnowledgeTools
from agno.tools import Toolkit
from core.knowledge import get_knowledge_base

logger = logging.getLogger(__name__)


def create_knowledge_tools() -> KnowledgeTools | None:
    """Create Agno's built-in KnowledgeTools with full capabilities."""
    kb = get_knowledge_base()
    if kb is None:
        return None
    
    return KnowledgeTools(
        knowledge=kb,
        enable_think=True,      # Plan search queries
        enable_search=True,     # Execute searches
        enable_analyze=True,    # Evaluate results
        add_few_shot=True,
    )


class KnowledgeIngestTools(Toolkit):
    """Tools for adding Discord attachments/URLs to knowledge base."""
    
    def __init__(self):
        super().__init__(name="knowledge_ingest")
        self.kb = get_knowledge_base()
        if self.kb:
            self.register(self.add_attachment)
            self.register(self.add_url)
    
    async def add_attachment(
        self, 
        url: str, 
        filename: str, 
        metadata: dict = None
    ) -> str:
        """
        Download a Discord attachment and add it to the knowledge base.
        
        Args:
            url: The CDN URL of the Discord attachment
            filename: Original filename (used to determine file type)
            metadata: Optional metadata (topic, user_id, type)
        
        Returns:
            Success or failure message
        """
        if not self.kb:
            return "❌ Knowledge base not configured"
        
        try:
            # Download to temp file
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return f"❌ Failed to download: HTTP {resp.status}"
                    content = await resp.read()
            
            # Save to temp file with original extension
            ext = os.path.splitext(filename)[1] or ".txt"
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                f.write(content)
                temp_path = f.name
            
            # Add to knowledge base
            await self.kb.add_content_async(
                path=temp_path,
                metadata=metadata or {}
            )
            os.unlink(temp_path)  # Cleanup
            
            logger.info(f"[Knowledge] Added attachment: {filename}")
            return f"✅ Added '{filename}' to knowledge base"
        except Exception as e:
            logger.error(f"[Knowledge] Failed to add attachment: {e}")
            return f"❌ Failed: {e}"
    
    async def add_url(self, url: str, metadata: dict = None) -> str:
        """
        Add content from a URL (webpage, PDF) to the knowledge base.
        
        Args:
            url: The URL to fetch and add
            metadata: Optional metadata (topic, source, type)
        
        Returns:
            Success or failure message
        """
        if not self.kb:
            return "❌ Knowledge base not configured"
        
        try:
            await self.kb.add_content_async(url=url, metadata=metadata or {})
            logger.info(f"[Knowledge] Added URL: {url}")
            return f"✅ Added content from {url}"
        except Exception as e:
            logger.error(f"[Knowledge] Failed to add URL: {e}")
            return f"❌ Failed: {e}"
