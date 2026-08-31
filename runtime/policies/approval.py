from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..actions import ToolCall


class ApprovalDecision(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class ApprovalOutcome:
    decision: ApprovalDecision
    reason: str


class ToolApprovalPolicy:
    """Decides whether a requested tool call may reach the executor."""

    def __init__(
        self,
        *,
        allow_file_writes: bool = False,
        allow_shell: bool = False,
        denied_tools: frozenset[str] = frozenset(),
    ) -> None:
        self.allow_file_writes = allow_file_writes
        self.allow_shell = allow_shell
        self.denied_tools = denied_tools

    def evaluate(self, call: ToolCall) -> ApprovalOutcome:
        if call.name in self.denied_tools:
            return ApprovalOutcome(ApprovalDecision.DENY, "tool_explicitly_denied")

        if call.name == "shell":
            if self.allow_shell:
                return ApprovalOutcome(ApprovalDecision.ALLOW, "shell_preapproved_for_run")
            return ApprovalOutcome(ApprovalDecision.REQUIRE_APPROVAL, "shell_requires_approval")

        if call.name == "file" and call.arguments.get("operation") == "write":
            if self.allow_file_writes:
                return ApprovalOutcome(ApprovalDecision.ALLOW, "file_write_preapproved_for_run")
            return ApprovalOutcome(ApprovalDecision.REQUIRE_APPROVAL, "file_write_requires_approval")

        if call.name == "file" and call.arguments.get("operation") in {"read", "list"}:
            return ApprovalOutcome(ApprovalDecision.ALLOW, "known_read_only_file_operation")

        return ApprovalOutcome(ApprovalDecision.REQUIRE_APPROVAL, "unclassified_tool_requires_approval")
