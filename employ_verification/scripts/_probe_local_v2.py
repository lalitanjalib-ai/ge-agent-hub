"""Quick local probe of emp_verify_v2 executor."""
import asyncio
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT.parent))

from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Role, TextPart, Part
from a2a.server.agent_execution import RequestContext


class _FakeMessage:
    def __init__(self):
        self.metadata = {}
        self.parts = [Part(root=TextPart(text="hi"))]


class _FakeContext(RequestContext):
    def __init__(self):
        self.message = _FakeMessage()
        self.task_id = str(uuid.uuid4())
        self.context_id = str(uuid.uuid4())
        self.call_context = None

    def get_user_input(self):
        return "hi"


async def main():
    from agents.emp_verify_v2.executor import EmpVerifyV2Executor

    executor = EmpVerifyV2Executor()
    queue = EventQueue()
    ctx = _FakeContext()
    await executor.execute(ctx, queue)
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
