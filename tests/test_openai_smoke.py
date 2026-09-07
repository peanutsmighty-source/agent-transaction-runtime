from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from runtime.actions import FinalAnswer
from runtime.context import ContextBuilder
from runtime.loop import AgentLoop, RunConfig
from runtime.model import ResponsesModelClient
from runtime.state import AgentState, AgentStatus
from runtime import CommandTaskVerifier, VerificationResult
from runtime.policies import ToolApprovalPolicy
from runtime.tools import FileTool, ToolRegistry, ToolRuntime


SMOKE_ENABLED = (
    os.environ.get("AGENT_RUNTIME_RESPONSES_SMOKE") == "1"
    or os.environ.get("AGENT_RUNTIME_OPENAI_SMOKE") == "1"
)
SMOKE_MODEL = os.environ.get("RESPONSES_SMOKE_MODEL") or os.environ.get("OPENAI_SMOKE_MODEL")
SMOKE_BASE_URL = os.environ.get("RESPONSES_SMOKE_BASE_URL") or os.environ.get("OPENAI_SMOKE_BASE_URL")
SMOKE_PROVIDER_NAME = os.environ.get("RESPONSES_SMOKE_PROVIDER", "responses-smoke")
SMOKE_API_KEY = os.environ.get("RESPONSES_API_KEY") or os.environ.get("OPENAI_API_KEY")
HAS_API_KEY = bool(SMOKE_API_KEY)


class MultiFileDiagnosisVerifier:
    async def verify(
        self,
        task: str,
        candidate_answer: str,
        state: AgentState,
    ) -> VerificationResult:
        answer = candidate_answer.lower()
        failures = []
        if "api_url" not in answer:
            failures.append("missing_api_url_reference")
        if "hard-coded" not in answer and "hardcoded" not in answer:
            failures.append("missing_hardcoded_cause")
        if any(
            execution.arguments.get("operation") == "write"
            for execution in state.tool_history
        ):
            failures.append("read_only_task_modified_workspace")
        return VerificationResult(
            passed=not failures,
            summary=(
                "Multi-file diagnosis passed."
                if not failures
                else "Multi-file diagnosis failed."
            ),
            details={"failures": failures},
        )


@pytest.mark.skipif(
    not (SMOKE_ENABLED and SMOKE_MODEL and HAS_API_KEY),
    reason=(
        "requires AGENT_RUNTIME_RESPONSES_SMOKE=1, RESPONSES_SMOKE_MODEL, "
        "and RESPONSES_API_KEY"
    ),
)
def test_real_responses_provider_smoke() -> None:
    async def run():
        client = ResponsesModelClient(
            SMOKE_MODEL or "",
            provider_name=SMOKE_PROVIDER_NAME,
            api_key=SMOKE_API_KEY,
            base_url=SMOKE_BASE_URL,
            timeout=30,
            max_retries=0,
        )
        try:
            context = ContextBuilder(
                "Reply with one short sentence and do not call tools."
            ).build(AgentState(task="Confirm that this API request succeeded."))
            return await client.generate(context, [])
        finally:
            await client.close()

    response = asyncio.run(run())

    assert isinstance(response.items[-1], FinalAnswer)
    assert response.items[-1].content.strip()
    assert response.response_id


@pytest.mark.skipif(
    not (SMOKE_ENABLED and SMOKE_MODEL and HAS_API_KEY),
    reason=(
        "requires AGENT_RUNTIME_RESPONSES_SMOKE=1, RESPONSES_SMOKE_MODEL, "
        "and RESPONSES_API_KEY"
    ),
)
def test_real_responses_agent_loop_tool_round_trip(tmp_path) -> None:
    (tmp_path / "smoke-marker.txt").write_text("provider is live", encoding="utf-8")

    async def run():
        client = ResponsesModelClient(
            SMOKE_MODEL or "",
            provider_name=SMOKE_PROVIDER_NAME,
            api_key=SMOKE_API_KEY,
            base_url=SMOKE_BASE_URL,
            timeout=30,
            max_retries=0,
        )
        registry = ToolRegistry()
        registry.register(FileTool(tmp_path))
        loop = AgentLoop(
            client,
            ToolRuntime(registry),
            ContextBuilder(
                "This is an agent-runtime integration test. You must call the file "
                "tool with operation=list and path=. before answering. After receiving "
                "the tool result, give a short final answer that mentions smoke-marker.txt."
            ),
            RunConfig(max_steps=3, trace_root=tmp_path / ".runs"),
        )
        try:
            return await loop.run("Inspect the test workspace using the file tool.")
        finally:
            await client.close()

    state = asyncio.run(run())

    assert state.status == AgentStatus.COMPLETED
    assert len(state.tool_history) == 1
    assert state.tool_history[0].name == "file"
    assert state.tool_history[0].result["success"] is True
    assert state.final_answer is not None
    assert "smoke-marker.txt" in state.final_answer


@pytest.mark.skipif(
    not (SMOKE_ENABLED and SMOKE_MODEL and HAS_API_KEY),
    reason=(
        "requires AGENT_RUNTIME_RESPONSES_SMOKE=1, RESPONSES_SMOKE_MODEL, "
        "and RESPONSES_API_KEY"
    ),
)
def test_real_responses_single_file_coding_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "calculator.py").write_text(
        "def add(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    acceptance_directory = tmp_path / "acceptance_tests"
    acceptance_directory.mkdir()
    (acceptance_directory / "test_calculator.py").write_text(
        "import unittest\n"
        "from calculator import add\n\n"
        "class CalculatorAcceptance(unittest.TestCase):\n"
        "    def test_positive_and_negative(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n"
        "        self.assertEqual(add(-2, 1), -1)\n",
        encoding="utf-8",
    )

    async def run():
        client = ResponsesModelClient(
            SMOKE_MODEL or "",
            provider_name=SMOKE_PROVIDER_NAME,
            api_key=SMOKE_API_KEY,
            base_url=SMOKE_BASE_URL,
            timeout=30,
            max_retries=0,
        )
        registry = ToolRegistry()
        registry.register(FileTool(workspace))
        loop = AgentLoop(
            client,
            ToolRuntime(registry),
            ContextBuilder(
                "You are in a coding-task acceptance test. Inspect calculator.py, "
                "fix the implementation using the file tool, and then give a short "
                "final answer. Do not modify acceptance_tests."
            ),
            RunConfig(max_steps=6, trace_root=tmp_path / ".runs"),
            approval_policy=ToolApprovalPolicy(allow_file_writes=True),
            task_verifier=CommandTaskVerifier(
                f'python -m unittest discover -s "{acceptance_directory}" -q',
                workspace,
                timeout_seconds=10,
            ),
        )
        try:
            return await loop.run(
                "Fix calculator.add so it works for positive and negative numbers."
            )
        finally:
            await client.close()

    state = asyncio.run(run())

    assert state.status == AgentStatus.COMPLETED
    assert any(
        execution.arguments.get("operation") == "write"
        and execution.arguments.get("path") == "calculator.py"
        for execution in state.tool_history
    )
    assert state.verification_history
    assert state.verification_history[-1].passed is True


@pytest.mark.skipif(
    not (SMOKE_ENABLED and SMOKE_MODEL and HAS_API_KEY),
    reason=(
        "requires AGENT_RUNTIME_RESPONSES_SMOKE=1, RESPONSES_SMOKE_MODEL, "
        "and RESPONSES_API_KEY"
    ),
)
def test_real_responses_read_only_multi_file_diagnosis(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "settings.py").write_text(
        'API_URL = "https://staging.example.test"\n',
        encoding="utf-8",
    )
    (workspace / "client.py").write_text(
        'from settings import API_URL\n\n'
        'def endpoint():\n'
        '    return "https://prod.example.test"  # bug\n',
        encoding="utf-8",
    )

    async def run():
        client = ResponsesModelClient(
            SMOKE_MODEL or "",
            provider_name=SMOKE_PROVIDER_NAME,
            api_key=SMOKE_API_KEY,
            base_url=SMOKE_BASE_URL,
            timeout=30,
            max_retries=0,
        )
        registry = ToolRegistry()
        registry.register(FileTool(workspace))
        loop = AgentLoop(
            client,
            ToolRuntime(registry),
            ContextBuilder(
                "This is a read-only coding diagnosis. Inspect the relevant Python "
                "files with the file tool, identify the exact cross-file cause, and "
                "do not modify any file."
            ),
            RunConfig(max_steps=5, trace_root=tmp_path / ".runs"),
            task_verifier=MultiFileDiagnosisVerifier(),
        )
        try:
            return await loop.run(
                "Diagnose why client.endpoint ignores the configured API endpoint."
            )
        finally:
            await client.close()

    state = asyncio.run(run())

    assert state.status == AgentStatus.COMPLETED
    assert state.verification_history[-1].passed is True
    read_paths = {
        execution.arguments.get("path")
        for execution in state.tool_history
        if execution.arguments.get("operation") == "read"
    }
    assert {"settings.py", "client.py"}.issubset(read_paths)


@pytest.mark.skipif(
    not (SMOKE_ENABLED and SMOKE_MODEL and HAS_API_KEY),
    reason=(
        "requires AGENT_RUNTIME_RESPONSES_SMOKE=1, RESPONSES_SMOKE_MODEL, "
        "and RESPONSES_API_KEY"
    ),
)
def test_real_responses_multi_file_implementation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pricing.py").write_text(
        "def discounted(price, percent):\n    raise NotImplementedError\n",
        encoding="utf-8",
    )
    (workspace / "report.py").write_text(
        "def format_total(value):\n    raise NotImplementedError\n",
        encoding="utf-8",
    )
    acceptance_directory = tmp_path / "acceptance_tests"
    acceptance_directory.mkdir()
    (acceptance_directory / "test_pricing_report.py").write_text(
        "import unittest\n"
        "from pricing import discounted\n"
        "from report import format_total\n\n"
        "class PricingReportAcceptance(unittest.TestCase):\n"
        "    def test_discounted_total(self):\n"
        "        self.assertEqual(discounted(100, 25), 75)\n"
        "        self.assertEqual(discounted(100, 0), 100)\n\n"
        "    def test_formatted_total(self):\n"
        "        self.assertEqual(format_total(75), '$75.00')\n"
        "        self.assertEqual(format_total(12.5), '$12.50')\n",
        encoding="utf-8",
    )

    async def run():
        client = ResponsesModelClient(
            SMOKE_MODEL or "",
            provider_name=SMOKE_PROVIDER_NAME,
            api_key=SMOKE_API_KEY,
            base_url=SMOKE_BASE_URL,
            timeout=30,
            max_retries=0,
        )
        registry = ToolRegistry()
        registry.register(FileTool(workspace))
        loop = AgentLoop(
            client,
            ToolRuntime(registry),
            ContextBuilder(
                "This is a coding-task acceptance test. Inspect and implement both "
                "requested Python modules with the file tool, then give a concise "
                "final answer. Hidden acceptance tests are outside your workspace."
            ),
            RunConfig(max_steps=8, trace_root=tmp_path / ".runs"),
            approval_policy=ToolApprovalPolicy(allow_file_writes=True),
            task_verifier=CommandTaskVerifier(
                f'python -m unittest discover -s "{acceptance_directory}" -q',
                workspace,
                timeout_seconds=10,
            ),
        )
        try:
            return await loop.run(
                "Implement pricing.discounted and report.format_total. "
                "format_total must return US-dollar text with exactly two decimals."
            )
        finally:
            await client.close()

    state = asyncio.run(run())

    assert state.status == AgentStatus.COMPLETED
    assert state.verification_history[-1].passed is True
    written_paths = {
        execution.arguments.get("path")
        for execution in state.tool_history
        if execution.arguments.get("operation") == "write"
    }
    assert {"pricing.py", "report.py"}.issubset(written_paths)


@pytest.mark.skipif(
    not (SMOKE_ENABLED and SMOKE_MODEL and HAS_API_KEY),
    reason=(
        "requires AGENT_RUNTIME_RESPONSES_SMOKE=1, RESPONSES_SMOKE_MODEL, "
        "and RESPONSES_API_KEY"
    ),
)
def test_real_responses_repairs_after_verifier_rejects_first_fix(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "calculator.py").write_text(
        "def add(a, b):\n    return 0\n",
        encoding="utf-8",
    )
    acceptance_directory = tmp_path / "acceptance_tests"
    acceptance_directory.mkdir()
    (acceptance_directory / "test_calculator.py").write_text(
        "import unittest\n"
        "from calculator import add\n\n"
        "class CalculatorAcceptance(unittest.TestCase):\n"
        "    def test_positive_and_negative(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n"
        "        self.assertEqual(add(-2, 1), -1)\n",
        encoding="utf-8",
    )

    async def run():
        client = ResponsesModelClient(
            SMOKE_MODEL or "",
            provider_name=SMOKE_PROVIDER_NAME,
            api_key=SMOKE_API_KEY,
            base_url=SMOKE_BASE_URL,
            timeout=30,
            max_retries=0,
        )
        registry = ToolRegistry()
        registry.register(FileTool(workspace))
        loop = AgentLoop(
            client,
            ToolRuntime(registry),
            ContextBuilder(
                "This is a deterministic recovery fault-injection test. On your first "
                "attempt, deliberately write a naive add implementation that returns "
                "abs(a) + abs(b), then give a final answer so the external verifier "
                "rejects it. After you receive the real verification failure, use its "
                "evidence to replace the implementation with correct signed addition "
                "and give a second final answer. Use only the file tool."
            ),
            RunConfig(max_steps=8, trace_root=tmp_path / ".runs"),
            approval_policy=ToolApprovalPolicy(allow_file_writes=True),
            task_verifier=CommandTaskVerifier(
                f'python -m unittest discover -s "{acceptance_directory}" -q',
                workspace,
                timeout_seconds=10,
            ),
        )
        try:
            return await loop.run(
                "Exercise the verifier-feedback recovery path while fixing calculator.add."
            )
        finally:
            await client.close()

    state = asyncio.run(run())

    assert state.status == AgentStatus.COMPLETED
    assert [attempt.passed for attempt in state.verification_history] == [False, True]
    writes = [
        execution
        for execution in state.tool_history
        if execution.arguments.get("operation") == "write"
        and execution.arguments.get("path") == "calculator.py"
    ]
    assert len(writes) >= 2
    assert "a + b" in (workspace / "calculator.py").read_text(encoding="utf-8")
