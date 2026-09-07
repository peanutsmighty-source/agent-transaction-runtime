from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .state import AgentState, Message


class ContextItemType(StrEnum):
    SYSTEM = "system"
    TASK = "task"
    SUMMARY = "summary"
    ASSISTANT = "assistant"
    VERIFICATION = "verification"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"


@dataclass(frozen=True)
class ContextItem:
    id: str
    type: ContextItemType
    content: str
    token_count: int
    name: str | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return {"id": self.id, "type": self.type.value, "content": self.content, "token_count": self.token_count, "name": self.name, "tool_call_id": self.tool_call_id}


@dataclass(frozen=True)
class ContextUnit:
    """A sequence of context items that compaction must keep or remove together."""

    items: tuple[ContextItem, ...]

    @property
    def token_count(self) -> int:
        return sum(item.token_count for item in self.items)

    @property
    def item_ids(self) -> list[str]:
        return [item.id for item in self.items]


def group_context_units(items: list[ContextItem]) -> list[ContextUnit]:
    """Keep a complete tool-call batch and all of its results atomic."""

    units: list[ContextUnit] = []
    index = 0
    while index < len(items):
        item = items[index]
        grouped = [item]
        if item.type == ContextItemType.TOOL_CALL and item.tool_call_id is not None:
            call_ids = {item.tool_call_id}
            next_index = index + 1
            while (
                next_index < len(items)
                and items[next_index].type == ContextItemType.TOOL_CALL
            ):
                grouped.append(items[next_index])
                if items[next_index].tool_call_id is not None:
                    call_ids.add(items[next_index].tool_call_id)
                next_index += 1
            while (
                next_index < len(items)
                and items[next_index].type == ContextItemType.TOOL_RESULT
                and items[next_index].tool_call_id in call_ids
            ):
                grouped.append(items[next_index])
                next_index += 1
            index = next_index
        else:
            index += 1
        units.append(ContextUnit(items=tuple(grouped)))
    return units


@dataclass
class ModelContext:
    items: list[ContextItem]

    @property
    def estimated_tokens(self) -> int:
        return sum(item.token_count for item in self.items)

    @property
    def messages(self) -> list[Message]:
        role_by_type = {
            ContextItemType.SYSTEM: "system",
            ContextItemType.TASK: "user",
            ContextItemType.SUMMARY: "assistant",
            ContextItemType.ASSISTANT: "assistant",
            ContextItemType.VERIFICATION: "user",
            ContextItemType.TOOL_CALL: "assistant",
            ContextItemType.TOOL_RESULT: "tool",
        }
        return [
            Message(
                role=role_by_type[item.type],
                content=item.content,
                name=item.name,
                tool_call_id=item.tool_call_id,
            )
            for item in self.items
        ]

    def to_dict(self) -> dict:
        return {"items": [item.to_dict() for item in self.items], "estimated_tokens": self.estimated_tokens}


class ContextBuilder:
    """Derives the model input from runtime state without mutating that state."""

    def __init__(self, system_instruction: str = "You are a careful coding agent.") -> None:
        self.system_instruction = system_instruction

    def build(self, state: AgentState) -> ModelContext:
        items = [
            self._item("system", ContextItemType.SYSTEM, self.system_instruction),
            self._item("task", ContextItemType.TASK, state.task),
        ]
        for index, message in enumerate(state.messages):
            if message.role == "verification":
                item_type = ContextItemType.VERIFICATION
            elif message.role == "tool":
                item_type = ContextItemType.TOOL_RESULT
            elif message.tool_call_id is not None:
                item_type = ContextItemType.TOOL_CALL
            else:
                item_type = ContextItemType.ASSISTANT
            items.append(self._item(f"message_{index}", item_type, message.content, message.name, message.tool_call_id))
        return ModelContext(items=items)

    def _item(self, id: str, type: ContextItemType, content: str, name: str | None = None, tool_call_id: str | None = None) -> ContextItem:
        return ContextItem(id=id, type=type, content=content, token_count=estimate_tokens(content), name=name, tool_call_id=tool_call_id)


def estimate_tokens(content: str) -> int:
    """Estimate tokens without coupling context policies to a model provider."""

    # Deliberately provider-agnostic. A provider tokenizer can replace this later.
    return max(1, (len(content) + 3) // 4) if content else 0
