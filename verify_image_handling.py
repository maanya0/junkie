import sys
from unittest.mock import MagicMock

# Mock modules to avoid import errors
mock_agent_factory = MagicMock()
sys.modules["agent"] = MagicMock()
sys.modules["agent.agent_factory"] = mock_agent_factory

mock_tools_factory = MagicMock()
sys.modules["tools"] = MagicMock()
sys.modules["tools.tools_factory"] = mock_tools_factory

import asyncio
from unittest.mock import patch
from discord_bot.chat_handler import async_ask_junkie
from agno.media import Image

async def test_image_passing():
    print("Testing image passing in async_ask_junkie...")
    
    # Mock the team and its arun method
    mock_team = MagicMock()
    
    # Mock the result object
    mock_result = MagicMock()
    mock_result.content = "I see the image."
    
    # Setup async mock for arun
    future = asyncio.Future()
    future.set_result(mock_result)
    mock_team.arun.return_value = future
    
    # Patch get_or_create_team to return our mock team
    with patch('discord_bot.chat_handler.get_or_create_team', return_value=mock_team):
        user_text = "Describe this image"
        user_id = "123"
        session_id = "456"
        images = [Image(url="http://example.com/image.png")]
        
        response = await async_ask_junkie(user_text, user_id, session_id, images=images)
        
        print(f"Response: {response}")
        
        # Verify arun was called with images
        mock_team.arun.assert_called_once()
        call_args = mock_team.arun.call_args
        print(f"Call args: {call_args}")
        
        assert call_args.kwargs['images'] == images
        assert call_args.kwargs['input'] == user_text
        assert call_args.kwargs['user_id'] == user_id
        assert call_args.kwargs['session_id'] == session_id
        
        print("SUCCESS: Images were passed correctly to team.arun")

if __name__ == "__main__":
    asyncio.run(test_image_passing())
