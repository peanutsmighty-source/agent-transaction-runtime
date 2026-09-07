from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from .actions import AssistantMessage, FinalAnswer, ReasoningSummary, ToolCall
from .context import ContextBuilder
from .loop import AgentLoop, RunConfig
from .model import FakeModelClient, ModelResponse, ModelUsage, ResponsesModelClient
from .observer import ContextObserver
from .policies import SlidingWindowCompaction, ToolApprovalPolicy
from .sandbox import DockerSandboxRunner, HostRunner
from .tools import FileTool, ShellTool, ToolRegistry, ToolRuntime


def _load_responses(path: Path) -> list[ModelResponse]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    responses: list[ModelResponse] = []
    for response in raw:
        items = []
        for item in response["items"]:
            if item["type"] == "tool_call":
                items.append(ToolCall(call_id=item["call_id"], name=item["name"], arguments=item.get("arguments", {})))
            elif item["type"] == "assistant_message":
                items.append(AssistantMessage(content=item["content"]))
            elif item["type"] == "reasoning_summary":
                items.append(ReasoningSummary(content=item["content"]))
            elif item["type"] == "final_answer":
                items.append(FinalAnswer(content=item["content"]))
            else:
                raise ValueError(f"unknown_response_item_type: {item['type']}")
        responses.append(
            ModelResponse(
                response_id=response.get("response_id"),
                items=items,
                usage=ModelUsage(**response.get("usage", {})),
            )
        )
    return responses


def _read_api_key(args: argparse.Namespace) -> str:
    if args.api_key_file is not None:
        key = args.api_key_file.read_text(encoding="utf-8").strip()
        if not key:
            raise ValueError("responses_api_key_file_is_empty")
        return key
    key = os.environ.get(args.api_key_env, "").strip()
    if not key:
        raise ValueError(f"responses_api_key_env_is_missing: {args.api_key_env}")
    return key


async def _run(args: argparse.Namespace) -> int:
    workspace = args.workspace.resolve()
    if args.shell_runner == "docker":
        runner = DockerSandboxRunner(
            workspace,
            image=args.docker_image,
            allow_network=args.allow_sandbox_network,
        )
    else:
        runner = HostRunner()
    registry = ToolRegistry()
    registry.register(FileTool(workspace))
    registry.register(ShellTool(workspace, timeout_seconds=args.tool_timeout, runner=runner))
    if args.provider == "fake":
        if args.responses is None:
            raise ValueError("--responses is required when --provider=fake")
        model = FakeModelClient(_load_responses(args.responses))
    else:
        if not args.model:
            raise ValueError("--model is required when --provider=responses")
        provider_name = args.provider_name or (
            "openai" if args.provider == "openai" else "responses"
        )
        model = ResponsesModelClient(
            args.model,
            provider_name=provider_name,
            api_key=_read_api_key(args),
            base_url=args.api_base_url,
            timeout=args.model_timeout,
            max_retries=args.model_max_retries,
        )
    loop = AgentLoop(
        model=model,
        tools=ToolRuntime(registry),
        context_builder=ContextBuilder(),
        config=RunConfig(
            max_steps=args.max_steps,
            trace_root=args.trace_root,
            context_window_tokens=args.context_window_tokens,
            reserved_output_tokens=args.reserved_output_tokens,
            compact_threshold=args.compact_threshold,
            max_tool_output_chars=args.max_tool_output_chars,
            compaction_target_ratio=args.compaction_target_ratio,
        ),
        approval_policy=ToolApprovalPolicy(
            allow_file_writes=args.allow_file_writes,
            allow_shell=args.allow_shell,
        ),
        compaction_policy=(
            SlidingWindowCompaction(min_recent_units=args.compaction_min_recent_units)
            if args.compaction == "sliding-window"
            else None
        ),
    )
    try:
        state = await loop.run(args.task)
    finally:
        if isinstance(model, ResponsesModelClient):
            await model.close()
    print(json.dumps(state.summary(), ensure_ascii=False, indent=2))
    if args.show_context:
        print()
        print(ContextObserver(args.context_window_tokens).format(state.context_stats))
        budget = state.context_budget
        print(f"Budget: effective input {budget.effective_input_tokens}, compaction trigger {budget.compact_trigger_tokens}, compaction triggered: {budget.should_compact}")
    return 0 if state.status.value == "completed" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the explicit Agent Runtime Lab loop.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("task")
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument(
        "--provider",
        choices=("fake", "responses", "openai"),
        default="fake",
        help="Model adapter; openai is a backward-compatible alias for responses",
    )
    run.add_argument("--responses", type=Path, help="JSON actions for --provider=fake")
    run.add_argument("--model", help="Model ID required by --provider=responses")
    run.add_argument("--provider-name", help="Provider label written to errors and trace")
    run.add_argument(
        "--api-base-url",
        "--openai-base-url",
        dest="api_base_url",
        help="Responses-compatible API base URL",
    )
    key_source = run.add_mutually_exclusive_group()
    key_source.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Environment variable containing the API key",
    )
    key_source.add_argument(
        "--api-key-file",
        type=Path,
        help="Plaintext key file outside the repository; environment variables are safer",
    )
    run.add_argument("--model-timeout", type=float, default=60)
    run.add_argument("--model-max-retries", type=int, default=2)
    run.add_argument("--max-steps", type=int, default=20)
    run.add_argument("--tool-timeout", type=float, default=15)
    run.add_argument(
        "--shell-runner",
        choices=("host", "docker"),
        default="host",
        help="Command execution backend; host is explicitly not isolated",
    )
    run.add_argument(
        "--docker-image",
        default="python:3.12-slim",
        help="Locally available image used by --shell-runner docker",
    )
    run.add_argument(
        "--allow-sandbox-network",
        action="store_true",
        help="Allow Docker sandbox outbound networking; default is no network",
    )
    run.add_argument("--context-window-tokens", type=int, default=128_000)
    run.add_argument("--reserved-output-tokens", type=int, default=16_000)
    run.add_argument("--compact-threshold", type=float, default=0.75)
    run.add_argument("--max-tool-output-chars", type=int, default=4_000)
    run.add_argument(
        "--compaction",
        choices=("none", "sliding-window"),
        default="none",
        help="Model-context compaction strategy",
    )
    run.add_argument("--compaction-target-ratio", type=float, default=0.5)
    run.add_argument("--compaction-min-recent-units", type=int, default=2)
    run.add_argument("--allow-file-writes", action="store_true", help="Pre-approve FileTool write operations for this run")
    run.add_argument("--allow-shell", action="store_true", help="Pre-approve ShellTool calls for this run; this is not a sandbox")
    run.add_argument("--show-context", action="store_true")
    run.add_argument("--trace-root", type=Path, default=Path(".runs"))
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "run":
        if args.provider == "fake" and args.responses is None:
            parser.error("--responses is required when --provider=fake")
        if args.provider != "fake" and not args.model:
            parser.error("--model is required when --provider=responses")
        raise SystemExit(asyncio.run(_run(args)))
