"""SDK session helpers for Strix agents."""

from pathlib import Path

from agents.memory import SQLiteSession


def open_agent_session(agent_id: str, path: Path) -> SQLiteSession:
    path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteSession(session_id=agent_id, db_path=path)
