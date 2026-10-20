import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from app.core.exceptions import AgentStepLimitError, LLMOutputError, ToolError
from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    function: Callable[..., dict]

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict
    ok: bool


class AgentResult(BaseModel):
    reply: str
    tool_calls: list[ToolCallRecord]
    steps: int


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, name: str, raw_arguments: str) -> tuple[dict, ToolCallRecord]:
        tool = self._tools.get(name)
        if tool is None:
            return failure(name, raw_arguments, f"Unknown tool: {name}")
        try:
            args = tool.args_model.model_validate_json(raw_arguments)
        except ValidationError as error:
            return failure(name, raw_arguments, f"Invalid arguments: {describe(error)}")
        arguments = args.model_dump(mode="json")
        try:
            result = tool.function(**args.model_dump())
        except ToolError as error:
            logger.warning("Tool failed: name=%s error=%s", name, error)
            record = ToolCallRecord(name=name, arguments=arguments, ok=False)
            return {"error": str(error)}, record
        logger.info("Tool succeeded: name=%s", name)
        return result, ToolCallRecord(name=name, arguments=arguments, ok=True)


async def run_agent(
    llm_client: LLMClient,
    registry: ToolRegistry,
    system: str,
    message: str,
    max_steps: int,
) -> AgentResult:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": message},
    ]
    schemas = registry.schemas()
    records = []
    for step in range(1, max_steps + 1):
        reply = await llm_client.chat(messages, schemas)
        if not reply.tool_calls:
            if not reply.content:
                raise LLMOutputError("Model returned an empty reply")
            return AgentResult(reply=reply.content, tool_calls=records, steps=step)

        messages.append(reply.model_dump(exclude_none=True))
        for call in reply.tool_calls:
            result, record = registry.execute(
                call.function.name, call.function.arguments
            )
            records.append(record)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}
            )
    raise AgentStepLimitError(f"Agent did not finish within {max_steps} steps")


def failure(name: str, raw_arguments: str, error: str) -> tuple[dict, ToolCallRecord]:
    logger.warning("Tool call rejected: name=%s error=%s", name, error)
    record = ToolCallRecord(
        name=name, arguments=loose_arguments(raw_arguments), ok=False
    )
    return {"error": error}, record


def describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in issue['loc']) or 'input'}: {issue['msg']}"
        for issue in error.errors()
    )


def loose_arguments(raw_arguments: str) -> dict:
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
