import logging
from agno.tools.mcp import MultiMCPTools
from core.config import MCP_URLS

# MCP tools - lazy initialization to avoid startup overhead
_mcp_tools = None
_mcp_connected = False

def get_mcp_tools():
    """Lazy initialization of MCP tools - only create when needed."""
    global _mcp_tools
    if _mcp_tools is None:
        if MCP_URLS:
            # Support comma-separated URLs from env
            urls = [url.strip() for url in MCP_URLS.split(",") if url.strip()]
        else:
            # Default fallback
            urls = [
                "https://litellm1-production-4090.up.railway.app/mcp/",
            ]
        
        if urls:
            _mcp_tools = MultiMCPTools(
                urls=urls,
                urls_transports=["streamable-http"],
            )
        else:
            # Return empty list if no MCP URLs configured
            _mcp_tools = []
    return _mcp_tools

async def setup_mcp():
    """Lazy connect to MCP tools only if they exist."""
    global _mcp_connected
    mcp = get_mcp_tools()
    if mcp and not _mcp_connected:
        try:
            if isinstance(mcp, MultiMCPTools):
                await mcp.connect()
                _mcp_connected = True
                logger = logging.getLogger(__name__)
                logger.info("MCP tools connected")
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to connect MCP tools: {e}")
