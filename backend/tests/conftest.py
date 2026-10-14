"""Test setup: an isolated data directory and a scripted fake LLM, so tests never touch real keys or quota."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.mkdtemp(prefix="deskmates-test-")
os.environ["CASTCALL_DATA_DIR"] = _TMP  # must be set before the app modules are imported
os.environ["GEMINI_API_KEYS"] = ""  # tests must never use real keys or quota
os.environ["GROQ_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["CEREBRAS_API_KEY"] = ""
os.environ["EMBEDDINGS"] = "none"  # unit tests never load a real embedding model
os.environ["CASTCALL_ENV"] = "dev"
os.environ["SCHEDULER"] = "off"  # tests drive the scheduler by hand
os.environ["ALLOW_PRIVATE_HOSTS"] = ""
os.environ["DAILY_LIMIT"] = "1000"
os.environ["MINUTE_LIMIT"] = "1000"

from langchain_core.messages import AIMessage  # noqa: E402


class FakeLLM:
    """Stands in for get_llm(): replays scripted replies (or structured objects) in order."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.tools_seen = []  # tool names offered on each get_llm() call

    async def ainvoke(self, messages, config=None):
        self.calls.append(messages)
        return self.replies.pop(0) if self.replies else AIMessage(content="ok [emotion: neutral]")


@pytest.fixture()
def fake_llm(monkeypatch):
    """Usage: llm = fake_llm([AIMessage(...), ...]); every get_llm() call returns the same scripted model."""
    import agent

    def install(replies):
        f = FakeLLM(replies)

        def getter(tools=None, **kw):
            f.tools_seen.append([t.name for t in (tools or [])])
            return f

        monkeypatch.setattr(agent, "get_llm", getter)
        return f

    return install


def _wipe_data_dir():
    """Remove databases and uploads so every test starts from nothing."""
    import shutil

    for f in Path(_TMP).glob("*.db*"):
        f.unlink(missing_ok=True)
    shutil.rmtree(Path(_TMP) / "uploads", ignore_errors=True)
    (Path(_TMP) / "custom_agents.json").unlink(missing_ok=True)


@pytest.fixture()
def clean_db():
    import db

    db.reset_for_tests()
    _wipe_data_dir()
    yield db
    db.reset_for_tests()
    _wipe_data_dir()
