
import asyncio
from unittest.mock import MagicMock
from tools.trigger_tools import TriggerTools

class MockAgent:
    def __init__(self, user_id=None, session_state=None):
        self.user_id = user_id
        self.session_state = session_state or {}

class MockTeam:
    def __init__(self, user_id=None, session_state=None):
        self.user_id = user_id
        self.session_state = session_state or {}

async def verify():
    tools = TriggerTools()
    print("--- Verifying TriggerTools Context Resolution ---")

    # Case 1: Agent with user_id and session_state
    agent1 = MockAgent(user_id="u1", session_state={"channel_id": "c1"})
    uid, cid = tools._get_context(agent=agent1)
    print(f"Case 1 (Agent): user_id={uid}, channel_id={cid} -> {'PASS' if uid=='u1' and cid=='c1' else 'FAIL'}")

    # Case 2: Team with user_id and session_state
    team1 = MockTeam(user_id="u2", session_state={"channel_id": "c2"})
    uid, cid = tools._get_context(team=team1)
    print(f"Case 2 (Team direct): user_id={uid}, channel_id={cid} -> {'PASS' if uid=='u2' and cid=='c2' else 'FAIL'}")

    # Case 3: Team WITHOUT user_id, but with user_id in session_state
    # This matches the Agno scenario where Team is generic but session_state is user-specific
    team2 = MockTeam(user_id=None, session_state={"user_id": "u3", "channel_id": "c3"})
    uid, cid = tools._get_context(team=team2)
    print(f"Case 3 (Team session_state fallback): user_id={uid}, channel_id={cid} -> {'PASS' if uid=='u3' and cid=='c3' else 'FAIL'}")

    # Case 4: Explicit user_id and session_state passed directly (e.g. from orchestrator)
    uid, cid = tools._get_context(user_id="u4", session_state={"channel_id": "c4"})
    print(f"Case 4 (Explicit Args): user_id={uid}, channel_id={cid} -> {'PASS' if uid=='u4' and cid=='c4' else 'FAIL'}")

    # Case 5: Agno injection simulation (Team context)
    # Agno typically calls tool like: create_reminder(..., team=team_instance)
    # Verify _get_context works when called with kwargs
    team3 = MockTeam(user_id=None, session_state={"user_id": "u5", "channel_id": "c5"})
    uid, cid = tools._get_context(agent=None, team=team3, user_id=None, session_state=None)
    print(f"Case 5 (Agno Injection Simulation): user_id={uid}, channel_id={cid} -> {'PASS' if uid=='u5' and cid=='c5' else 'FAIL'}")

if __name__ == "__main__":
    asyncio.run(verify())
