from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .actions import AssistantMessage, FinalAnswer, ReasoningSummary, ToolCall
from .budget import ContextBudget
from .context import ContextBuilder
from .events import AgentEvent, EventType
from .model.base import ModelClient
from .observer import ContextObserver
from .policies import (
    ApprovalDecision,
    CompactionPolicy,
    RepeatedFailedActionDetector,
    ToolApprovalPolicy,
    ToolOutputPolicy,
)
from .state import AgentState, AgentStatus, ContextBudgetStatus, Message, ToolExecution
from .tools.runtime import ToolRuntime
from .tools.base import ToolResult
from .trace import TraceWriter


@dataclass(frozen=True)
class RunConfig:
    max_steps: int = 20
    trace_root: Path = Path(".runs")
    repeated_failed_action_threshold: int = 3
    context_window_tokens: int = 128_000
    reserved_output_tokens: int = 16_000
    compact_threshold: float = 0.75
    max_tool_output_chars: int = 4_000
    compaction_target_ratio: float = 0.5


class AgentLoop:
    """The intentionally explicit state machine that drives a single agent run."""

    def __init__(self, model: ModelClient, tools: ToolRuntime, context_builder: ContextBuilder, config: RunConfig | None = None, context_observer: ContextObserver | None = None, tool_output_policy: ToolOutputPolicy | None = None, context_budget: ContextBudget | None = None, approval_policy: ToolApprovalPolicy | None = None, compaction_policy: CompactionPolicy | None = None) -> None:
        self.model = model
        self.tools = tools
        self.context_builder = context_builder
        self.config = config or RunConfig()
        self.loop_detector = RepeatedFailedActionDetector(self.config.repeated_failed_action_threshold)
        self.context_observer = context_observer or ContextObserver(self.config.context_window_tokens)
        self.tool_output_policy = tool_output_policy or ToolOutputPolicy(self.config.max_tool_output_chars)
        self.context_budget = context_budget or ContextBudget(
            max_tokens=self.config.context_window_tokens,
            reserved_output_tokens=self.config.reserved_output_tokens,
            compact_threshold=self.config.compact_threshold,
        )
        self.approval_policy = approval_policy or ToolApprovalPolicy()
        if not 0 < self.config.compaction_target_ratio <= 1:
            raise ValueError("compaction_target_ratio_must_be_between_zero_and_one")
        self.compaction_policy = compaction_policy

    async def run(self, task: str) -> AgentState:
        state = AgentState(task=task)
        trace = TraceWriter(self.config.trace_root)
        trace.write_metadata(task)
        self._event(state, trace, EventType.USER_INPUT, {"task": task})

        while state.status == AgentStatus.RUNNING:
            if state.step >= self.config.max_steps:
                state.status = AgentStatus.FAILED
                state.error = "max_steps_exceeded"
                self._event(state, trace, EventType.AGENT_STOPPED, {"reason": state.error})
                break

            try:
                context = self.context_builder.build(state)
                budget_decision = self.context_budget.evaluate(context.estimated_tokens)
                state.context_budget = ContextBudgetStatus(**budget_decision.to_dict())
                self._event(state, trace, EventType.CONTEXT_BUDGET_CHECKED, budget_decision.to_dict())
                if budget_decision.should_compact:
                    self._event(state, trace, EventType.CONTEXT_COMPACTION_REQUIRED, budget_decision.to_dict())
                    if self.compaction_policy is not None:
                        trace.write_context(state.step, context, view="pre_compaction")
                        target_tokens = max(
                            1,
                            int(
                                budget_decision.effective_input_tokens
                                * self.config.compaction_target_ratio
                            ),
                        )
                        compaction = self.compaction_policy.compact(context, target_tokens)
                        context = compaction.context
                        if compaction.applied:
                            self._event(
                                state,
                                trace,
                                EventType.CONTEXT_COMPACTION_APPLIED,
                                compaction.to_event_data(),
                            )
                state.context_stats = self.context_observer.inspect(context)
                trace.write_context(state.step, context)
                self._event(state, trace, EventType.CONTEXT_BUILT, {"estimated_tokens": context.estimated_tokens, "item_count": len(context.messages)})
                self._event(state, trace, EventType.CONTEXT_OBSERVED, state.context_stats.to_dict())
                self._event(state, trace, EventType.MODEL_REQUEST, {"tool_count": len(self.tools.registry.schemas())})
                response = await self.model.generate(context, self.tools.registry.schemas())
                item_types = [type(item).__name__ for item in response.items]
                state.model_usage.add(response.usage.to_dict())
                self._event(
                    state,
                    trace,
                    EventType.MODEL_RESPONSE,
                    {"response_id": response.response_id, "item_types": item_types, "usage": response.usage.to_dict()},
                )

                for item in response.items:
                    self._event(state, trace, EventType.MODEL_RESPONSE_ITEM, self._response_item_data(item))
                    if isinstance(item, AssistantMessage):
                        state.messages.append(Message(role="assistant", content=item.content))
                    elif isinstance(item, ReasoningSummary):
                        state.messages.append(Message(role="assistant", name="reasoning_summary", content=item.content))
                    elif isinstance(item, ToolCall):
                        await self._execute_tool_call(state, trace, item)
                    elif isinstance(item, FinalAnswer):
                        state.final_answer = item.content
                        state.status = AgentStatus.COMPLETED
                    else:
                        raise TypeError(f"unsupported_response_item: {type(item).__name__}")

                state.step += 1
                if state.status == AgentStatus.COMPLETED:
                    self._event(state, trace, EventType.AGENT_STOPPED, {"reason": "final_answer"})
                    break
            except Exception as error:  # Runtime boundary: preserve the trace instead of leaking an exception.
                state.status = AgentStatus.FAILED
                state.error = str(error)
                self._event(state, trace, EventType.RUNTIME_ERROR, {"error": state.error})
                self._event(state, trace, EventType.AGENT_STOPPED, {"reason": "runtime_error"})

        trace.write_result(state)
        return state

    @staticmethod
    def _event(state: AgentState, trace: TraceWriter, type: EventType, data: dict) -> None:
        event = AgentEvent.create(type, state.step, data)
        state.events.append(event)
        trace.write_event(event)

    async def _execute_tool_call(self, state: AgentState, trace: TraceWriter, call: ToolCall) -> None:
        self._event(state, trace, EventType.TOOL_CALL, {"call_id": call.call_id, "name": call.name, "arguments": call.arguments})
        approval = self.approval_policy.evaluate(call)
        self._event(
            state,
            trace,
            EventType.TOOL_APPROVAL_DECIDED,
            {"call_id": call.call_id, "name": call.name, "decision": approval.decision.value, "reason": approval.reason},
        )
        if approval.decision == ApprovalDecision.ALLOW:
            result = await self.tools.execute(call)
        elif approval.decision == ApprovalDecision.REQUIRE_APPROVAL:
            result = ToolResult(success=False, output={}, error="approval_required")
        else:
            result = ToolResult(success=False, output={}, error="tool_denied")
        execution = ToolExecution(call_id=call.call_id, name=call.name, arguments=call.arguments, result=result.to_dict())
        state.tool_history.append(execution)
        trace.write_tool(state.step, execution)
        state.messages.append(Message(role="assistant", name=call.name, tool_call_id=call.call_id, content=f"tool_call: {call.arguments}"))
        rendered_output = self.tool_output_policy.render_for_context(result)
        state.messages.append(Message(role="tool", name=call.name, tool_call_id=call.call_id, content=rendered_output.content))
        self._event(
            state,
            trace,
            EventType.TOOL_RESULT,
            {"call_id": call.call_id, "name": call.name, "success": result.success, "error": result.error},
        )
        if rendered_output.truncated:
            self._event(
                state,
                trace,
                EventType.TOOL_OUTPUT_TRUNCATED,
                {
                    "call_id": call.call_id,
                    "name": call.name,
                    "original_tokens": rendered_output.original_tokens,
                    "retained_tokens": rendered_output.retained_tokens,
                    "truncated_tokens": rendered_output.truncated_tokens,
                },
            )
        warning = self.loop_detector.detect(state.tool_history)
        if warning:
            self._event(
                state,
                trace,
                EventType.POSSIBLE_LOOP_DETECTED,
                {"fingerprint": warning.fingerprint, "repetitions": warning.repetitions},
            )

    @staticmethod
    def _response_item_data(item: object) -> dict:
        data = {"item_type": type(item).__name__}
        if isinstance(item, ToolCall):
            data.update({"call_id": item.call_id, "name": item.name, "arguments": item.arguments})
        elif isinstance(item, (AssistantMessage, ReasoningSummary, FinalAnswer)):
            data["content"] = item.content
        return data
