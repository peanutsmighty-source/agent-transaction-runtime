from __future__ import annotations

import asyncio
from pathlib import Path

from runtime.sandbox import (
    CommandExecution,
    CommandRequest,
    DockerSandboxRunner,
    IsolationKind,
)
from runtime.tools import FileTool, ShellTool
from runtime.tools import ToolRegistry, ToolRuntime
from runtime.actions import ToolCall


def test_file_tool_rejects_parent_escape(tmp_path: Path) -> None:
    result = asyncio.run(FileTool(tmp_path).execute({"operation": "read", "path": "../secret.txt"}))
    assert result.success is False
    assert result.error == "path_outside_workspace"


def test_shell_tool_times_out(tmp_path: Path) -> None:
    result = asyncio.run(ShellTool(tmp_path, timeout_seconds=0.01).execute({"command": "python -c \"import time; time.sleep(1)\""}))
    assert result.success is False
    assert result.error == "tool_timeout"
    assert result.output["runner"] == "host"
    assert result.output["isolation"] == "none"


def test_shell_tool_delegates_execution_and_reports_isolation(tmp_path: Path) -> None:
    class RecordingRunner:
        name = "recording"
        isolation = IsolationKind.CONTAINER

        def __init__(self) -> None:
            self.request: CommandRequest | None = None

        async def run(self, request: CommandRequest) -> CommandExecution:
            self.request = request
            return CommandExecution(0, "hello\n", "", 7)

    runner = RecordingRunner()
    result = asyncio.run(
        ShellTool(tmp_path, timeout_seconds=3, runner=runner).execute(
            {"command": "echo hello", "cwd": "."}
        )
    )

    assert runner.request == CommandRequest("echo hello", tmp_path.resolve(), 3)
    assert result.success is True
    assert result.output["stdout"] == "hello\n"
    assert result.output["runner"] == "recording"
    assert result.output["isolation"] == "container"


def test_docker_runner_builds_fail_closed_container_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    subdirectory = workspace / "src"
    subdirectory.mkdir(parents=True)
    runner = DockerSandboxRunner(
        workspace,
        image="agent-lab:test",
        container_user="1000:1000",
    )

    argv = runner.build_argv(
        CommandRequest("python -m pytest", subdirectory, 10),
        container_name="agent-runtime-lab-test",
    )

    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    assert argv[argv.index("--user") + 1] == "1000:1000"
    assert argv[argv.index("--workdir") + 1] == "/workspace/src"
    assert argv[-4:] == ["agent-lab:test", "sh", "-lc", "python -m pytest"]
    assert not any(argument.startswith("SSH_") for argument in argv)


def test_docker_runner_can_only_map_cwd_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = DockerSandboxRunner(workspace, image="agent-lab:test")

    try:
        runner.build_argv(
            CommandRequest("echo unsafe", tmp_path, 10),
            container_name="agent-runtime-lab-test",
        )
    except ValueError as error:
        assert str(error) == "cwd_outside_docker_workspace"
    else:
        raise AssertionError("Docker runner accepted a cwd outside its workspace")


def test_missing_docker_executable_is_a_structured_tool_failure(tmp_path: Path) -> None:
    runner = DockerSandboxRunner(
        tmp_path,
        image="agent-lab:test",
        docker_executable="definitely-missing-docker-executable",
    )

    result = asyncio.run(
        ShellTool(tmp_path, runner=runner).execute({"command": "echo hello"})
    )

    assert result.success is False
    assert result.error == "shell_launch_failed"
    assert result.output["runner"] == "docker"
    assert result.output["isolation"] == "container"


def test_unknown_tool_is_a_structured_observation() -> None:
    result = asyncio.run(ToolRuntime(ToolRegistry()).execute(ToolCall("call_missing", "missing", {})))
    assert result.success is False
    assert result.error == "unknown_tool: missing"


def test_tool_schemas_use_responses_function_tool_shape(tmp_path: Path) -> None:
    file_schema = FileTool(tmp_path).schema()
    shell_schema = ShellTool(tmp_path).schema()

    assert file_schema["type"] == "function"
    assert file_schema["parameters"]["type"] == "object"
    assert file_schema["parameters"]["additionalProperties"] is False
    assert shell_schema["parameters"]["required"] == ["command"]
