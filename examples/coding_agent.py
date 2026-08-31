"""Run a deterministic coding-agent scenario without a model API key."""

import asyncio
from pathlib import Path

from runtime.actions import FinalAnswer, ToolCall
from runtime.context import ContextBuilder
from runtime.loop import AgentLoop
from runtime.model import FakeModelClient, ModelResponse
from runtime.policies import ToolApprovalPolicy
from runtime.tools import FileTool, ShellTool, ToolRegistry, ToolRuntime


async def main() -> None:
    workspace = Path(__file__).parent / "demo_project"
    registry = ToolRegistry()
    registry.register(FileTool(workspace))
    registry.register(ShellTool(workspace))
    model = FakeModelClient([
        ModelResponse(items=[ToolCall("call_read", "file", {"operation": "read", "path": "calculator.py"})]),
        ModelResponse(items=[ToolCall("call_write", "file", {"operation": "write", "path": "calculator.py", "content": "def add(left: int, right: int) -> int:\n    return left + right\n"})]),
        ModelResponse(items=[ToolCall("call_verify", "shell", {"command": "python -c \"from calculator import add; assert add(2, 3) == 5\""})]),
        ModelResponse(items=[FinalAnswer("Fixed calculator.add and verified it.")]),
    ])
    state = await AgentLoop(
        model,
        ToolRuntime(registry),
        ContextBuilder(),
        approval_policy=ToolApprovalPolicy(allow_file_writes=True, allow_shell=True),
    ).run("Fix calculator.add and verify it.")
    print(state.summary())


if __name__ == "__main__":
    asyncio.run(main())
