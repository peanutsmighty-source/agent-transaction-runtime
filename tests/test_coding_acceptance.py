from __future__ import annotations

import asyncio
from pathlib import Path

from runtime import (
    CommandTaskVerifier,
    CompositeTaskVerifier,
    FileRequirement,
    FileStateVerifier,
    VerificationResult,
)
from runtime.actions import FinalAnswer, ToolCall
from runtime.context import ContextBuilder, ContextItemType
from runtime.loop import AgentLoop, RunConfig
from runtime.model import FakeModelClient, ModelResponse
from runtime.policies import ToolApprovalPolicy
from runtime.state import AgentState, AgentStatus
from runtime.tools import FileTool, ShellTool, ToolRegistry, ToolRuntime


def write_files(workspace: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def acceptance_command(acceptance_directory: Path) -> str:
    return f'python -m unittest discover -s "{acceptance_directory}" -q'


def coding_loop(
    workspace: Path,
    model: FakeModelClient,
    *,
    verifier=None,
    max_steps: int = 10,
) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(FileTool(workspace))
    registry.register(ShellTool(workspace, timeout_seconds=10))
    return AgentLoop(
        model,
        ToolRuntime(registry),
        ContextBuilder(),
        RunConfig(max_steps=max_steps, trace_root=workspace / ".runs"),
        approval_policy=ToolApprovalPolicy(
            allow_file_writes=True,
            allow_shell=True,
        ),
        task_verifier=verifier,
    )


class ReadOnlyDiagnosisVerifier:
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
        if any(execution.arguments.get("operation") == "write" for execution in state.tool_history):
            failures.append("diagnosis_modified_files")
        return VerificationResult(
            passed=not failures,
            summary="Diagnosis contract passed." if not failures else "Diagnosis contract failed.",
            details={"failures": failures},
        )


def test_read_only_multi_file_diagnosis(tmp_path: Path) -> None:
    write_files(
        tmp_path,
        {
            "settings.py": 'API_URL = "https://staging.example.test"\n',
            "client.py": (
                'from settings import API_URL\n\n'
                'def endpoint():\n'
                '    return "https://prod.example.test"  # bug: ignores API_URL\n'
            ),
        },
    )
    model = FakeModelClient(
        [
            ModelResponse(
                items=[
                    ToolCall("read_settings", "file", {"operation": "read", "path": "settings.py"}),
                    ToolCall("read_client", "file", {"operation": "read", "path": "client.py"}),
                ]
            ),
            ModelResponse(
                items=[FinalAnswer("client.endpoint has a hard-coded URL and ignores API_URL.")]
            ),
        ]
    )

    state = asyncio.run(
        coding_loop(tmp_path, model, verifier=ReadOnlyDiagnosisVerifier()).run(
            "Diagnose why the client ignores the configured API endpoint; do not modify files."
        )
    )

    assert state.status == AgentStatus.COMPLETED
    assert [execution.name for execution in state.tool_history] == ["file", "file"]
    assert all(execution.result["success"] for execution in state.tool_history)


def test_single_file_bug_fix_runs_tests_and_passes_independent_verifier(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    acceptance_directory = tmp_path / "acceptance_tests"
    write_files(
        workspace,
        {
            "calculator.py": "def add(a, b):\n    return a - b\n",
        },
    )
    write_files(
        acceptance_directory,
        {
            "test_calculator.py": (
                "import unittest\n"
                "from calculator import add\n\n"
                "class CalculatorTests(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertEqual(add(2, 3), 5)\n"
            ),
        },
    )
    command = acceptance_command(acceptance_directory)
    model = FakeModelClient(
        [
            ModelResponse(items=[ToolCall("read_bug", "file", {"operation": "read", "path": "calculator.py"})]),
            ModelResponse(
                items=[
                    ToolCall(
                        "write_fix",
                        "file",
                        {"operation": "write", "path": "calculator.py", "content": "def add(a, b):\n    return a + b\n"},
                    )
                ]
            ),
            ModelResponse(items=[ToolCall("run_tests", "shell", {"command": command})]),
            ModelResponse(items=[FinalAnswer("Fixed add and the tests pass.")]),
        ]
    )
    verifier = CommandTaskVerifier(command, workspace, timeout_seconds=10)

    state = asyncio.run(
        coding_loop(workspace, model, verifier=verifier).run("Fix calculator.add and run tests.")
    )

    assert state.status == AgentStatus.COMPLETED
    assert state.tool_history[-1].result["success"] is True
    assert state.verification_history[-1].passed is True
    assert "a + b" in (workspace / "calculator.py").read_text(encoding="utf-8")


def test_failed_first_fix_is_repaired_after_test_and_verifier_feedback(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    acceptance_directory = tmp_path / "acceptance_tests"
    write_files(
        workspace,
        {
            "calculator.py": "def add(a, b):\n    return 0\n",
        },
    )
    write_files(
        acceptance_directory,
        {
            "test_calculator.py": (
                "import unittest\n"
                "from calculator import add\n\n"
                "class CalculatorTests(unittest.TestCase):\n"
                "    def test_positive_and_negative(self):\n"
                "        self.assertEqual(add(2, 3), 5)\n"
                "        self.assertEqual(add(-2, 1), -1)\n"
            ),
        },
    )
    command = acceptance_command(acceptance_directory)
    model = FakeModelClient(
        [
            ModelResponse(
                items=[ToolCall("bad_fix", "file", {"operation": "write", "path": "calculator.py", "content": "def add(a, b):\n    return abs(a) + abs(b)\n"})]
            ),
            ModelResponse(items=[ToolCall("first_tests", "shell", {"command": command})]),
            ModelResponse(items=[FinalAnswer("The first fix is complete.")]),
            ModelResponse(
                items=[ToolCall("good_fix", "file", {"operation": "write", "path": "calculator.py", "content": "def add(a, b):\n    return a + b\n"})]
            ),
            ModelResponse(items=[ToolCall("second_tests", "shell", {"command": command})]),
            ModelResponse(items=[FinalAnswer("Corrected the negative-number behavior.")]),
        ]
    )
    verifier = CommandTaskVerifier(command, workspace, timeout_seconds=10)

    state = asyncio.run(
        coding_loop(workspace, model, verifier=verifier).run(
            "Fix calculator.add for positive and negative numbers and run tests."
        )
    )

    assert state.status == AgentStatus.COMPLETED
    assert [attempt.passed for attempt in state.verification_history] == [False, True]
    fourth_request = model.requests[3]
    assert fourth_request.items[-1].type == ContextItemType.VERIFICATION
    assert "FAILED" in fourth_request.items[-1].content
    assert [execution.result["success"] for execution in state.tool_history] == [
        True,
        False,
        True,
        True,
    ]


def test_multi_file_implementation_and_test_update_pass_hidden_acceptance(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    acceptance_directory = tmp_path / "acceptance_tests"
    write_files(
        workspace,
        {
            "pricing.py": "def discounted(price, percent):\n    raise NotImplementedError\n",
            "test_pricing.py": "# Add repository-facing unit tests here.\n",
        },
    )
    write_files(
        acceptance_directory,
        {
            "test_pricing_acceptance.py": (
                "import unittest\n"
                "from pricing import discounted\n\n"
                "class PricingAcceptance(unittest.TestCase):\n"
                "    def test_discount_and_zero_percent(self):\n"
                "        self.assertEqual(discounted(100, 25), 75)\n"
                "        self.assertEqual(discounted(100, 0), 100)\n"
            ),
        },
    )
    command = acceptance_command(acceptance_directory)
    model = FakeModelClient(
        [
            ModelResponse(
                items=[
                    ToolCall(
                        "write_implementation",
                        "file",
                        {"operation": "write", "path": "pricing.py", "content": "def discounted(price, percent):\n    return price * (100 - percent) / 100\n"},
                    ),
                    ToolCall(
                        "write_tests",
                        "file",
                        {"operation": "write", "path": "test_pricing.py", "content": "from pricing import discounted\n\ndef test_zero_percent():\n    assert discounted(100, 0) == 100\n"},
                    ),
                ]
            ),
            ModelResponse(items=[ToolCall("run_acceptance", "shell", {"command": command})]),
            ModelResponse(items=[FinalAnswer("Implemented discount calculation and updated tests.")]),
        ]
    )
    verifier = CompositeTaskVerifier(
        [
            CommandTaskVerifier(command, workspace, timeout_seconds=10),
            FileStateVerifier(
                workspace,
                [FileRequirement("test_pricing.py", contains="test_zero_percent")],
            ),
        ]
    )

    state = asyncio.run(
        coding_loop(workspace, model, verifier=verifier).run(
            "Implement discounted and add a zero-percent regression test."
        )
    )

    assert state.status == AgentStatus.COMPLETED
    assert [execution.call_id for execution in state.tool_history[:2]] == [
        "write_implementation",
        "write_tests",
    ]
    assert state.verification_history[-1].passed is True
