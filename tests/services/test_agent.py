import copy
import json
import logging

import pytest
from openai.types.chat import (
    ChatCompletionMessage,
    ChatCompletionMessageFunctionToolCall,
)
from openai.types.chat.chat_completion_message_function_tool_call import Function
from pydantic import BaseModel

from app.core.exceptions import AgentStepLimitError, LLMOutputError, ToolError
from app.services.agent import Tool, ToolCallRecord, ToolRegistry, run_agent

pytestmark = pytest.mark.anyio


class LookupArgs(BaseModel):
    item_id: int


class FakeLLM:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    async def chat(self, messages, tools):
        self.calls.append({"messages": copy.deepcopy(messages), "tools": tools})
        return self._replies.pop(0)


def lookup(item_id):
    if item_id == 404:
        raise ToolError("Item 404 was not found")
    return {"item_id": item_id, "status": "in_stock"}


def broken(item_id):
    raise KeyError("bug")


def tool_call(name, arguments, call_id="call_1"):
    return ChatCompletionMessageFunctionToolCall(
        id=call_id, type="function", function=Function(name=name, arguments=arguments)
    )


def asks(*calls):
    return ChatCompletionMessage(role="assistant", content=None, tool_calls=list(calls))


def says(text):
    return ChatCompletionMessage(role="assistant", content=text)


@pytest.fixture
def registry():
    return ToolRegistry(
        [
            Tool("lookup", "Look up an item", LookupArgs, lookup),
            Tool("broken", "Always crashes", LookupArgs, broken),
        ]
    )


def tool_messages(call):
    return [m for m in call["messages"] if m["role"] == "tool"]


async def run(llm, registry, max_steps=5):
    return await run_agent(
        llm, registry, "system prompt", "is item 7 in stock?", max_steps
    )


def test_tool_schema_uses_args_model():
    tool = Tool("lookup", "Look up an item", LookupArgs, lookup)

    assert tool.schema() == {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Look up an item",
            "parameters": LookupArgs.model_json_schema(),
        },
    }


def test_registry_schemas_list_every_tool(registry):
    names = [schema["function"]["name"] for schema in registry.schemas()]

    assert names == ["lookup", "broken"]


def test_execute_success_returns_result_and_normalized_arguments(registry):
    result, record = registry.execute("lookup", '{"item_id": "7"}')

    assert result == {"item_id": 7, "status": "in_stock"}
    assert record == ToolCallRecord(name="lookup", arguments={"item_id": 7}, ok=True)


def test_execute_unknown_tool(registry):
    result, record = registry.execute("delete_item", '{"item_id": 1}')

    assert result == {"error": "Unknown tool: delete_item"}
    assert record == ToolCallRecord(
        name="delete_item", arguments={"item_id": 1}, ok=False
    )


def test_execute_wrong_argument_type(registry):
    result, record = registry.execute("lookup", '{"item_id": "abc"}')

    assert result["error"].startswith("Invalid arguments: item_id:")
    assert record.arguments == {"item_id": "abc"}
    assert record.ok is False


def test_execute_missing_argument(registry):
    result, record = registry.execute("lookup", "{}")

    assert result["error"] == "Invalid arguments: item_id: Field required"
    assert record.ok is False


def test_execute_malformed_json(registry):
    result, record = registry.execute("lookup", "not json")

    assert result["error"].startswith("Invalid arguments: input: Invalid JSON")
    assert record.arguments == {}


def test_execute_tool_error_is_returned_to_model(registry):
    result, record = registry.execute("lookup", '{"item_id": 404}')

    assert result == {"error": "Item 404 was not found"}
    assert record == ToolCallRecord(name="lookup", arguments={"item_id": 404}, ok=False)


def test_execute_unexpected_exception_propagates(registry):
    with pytest.raises(KeyError):
        registry.execute("broken", '{"item_id": 1}')


def test_rejected_calls_are_logged(registry, caplog):
    with caplog.at_level(logging.WARNING):
        registry.execute("delete_item", "{}")

    assert "Unknown tool: delete_item" in caplog.text


async def test_direct_answer_without_tools(registry):
    llm = FakeLLM([says("Hello!")])

    result = await run(llm, registry)

    assert result.reply == "Hello!"
    assert result.tool_calls == []
    assert result.steps == 1


async def test_first_call_sends_system_user_and_schemas(registry):
    llm = FakeLLM([says("Hello!")])

    await run(llm, registry)

    assert llm.calls[0]["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "is item 7 in stock?"},
    ]
    assert llm.calls[0]["tools"] == registry.schemas()


async def test_tool_call_then_answer(registry):
    llm = FakeLLM([asks(tool_call("lookup", '{"item_id": 7}')), says("In stock.")])

    result = await run(llm, registry)

    assert result.reply == "In stock."
    assert result.steps == 2
    assert result.tool_calls == [
        ToolCallRecord(name="lookup", arguments={"item_id": 7}, ok=True)
    ]


async def test_tool_result_follows_assistant_message_with_matching_id(registry):
    llm = FakeLLM(
        [asks(tool_call("lookup", '{"item_id": 7}', "call_9")), says("Done.")]
    )

    await run(llm, registry)

    second = llm.calls[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[2]["tool_calls"][0]["id"] == "call_9"
    assert second[3]["tool_call_id"] == "call_9"
    assert json.loads(second[3]["content"]) == {"item_id": 7, "status": "in_stock"}


async def test_multiple_tool_calls_in_one_turn(registry):
    llm = FakeLLM(
        [
            asks(
                tool_call("lookup", '{"item_id": 6}', "call_a"),
                tool_call("lookup", '{"item_id": 7}', "call_b"),
            ),
            says("Both found."),
        ]
    )

    result = await run(llm, registry)

    tools = tool_messages(llm.calls[1])
    assert [m["tool_call_id"] for m in tools] == ["call_a", "call_b"]
    assert [r.arguments["item_id"] for r in result.tool_calls] == [6, 7]
    assert result.steps == 2


@pytest.mark.parametrize(
    "bad_call, error",
    [
        (tool_call("delete_item", '{"item_id": 7}'), "Unknown tool"),
        (tool_call("lookup", '{"item_id": "abc"}'), "Invalid arguments"),
        (tool_call("lookup", '{"item_id": 404}'), "Item 404 was not found"),
    ],
)
async def test_model_recovers_after_failed_call(registry, bad_call, error):
    llm = FakeLLM(
        [
            asks(bad_call),
            asks(tool_call("lookup", '{"item_id": 7}', "call_2")),
            says("In stock."),
        ]
    )

    result = await run(llm, registry)

    assert error in json.loads(tool_messages(llm.calls[1])[0]["content"])["error"]
    assert [record.ok for record in result.tool_calls] == [False, True]
    assert result.reply == "In stock."
    assert result.steps == 3


async def test_unexpected_tool_exception_stops_the_run(registry):
    llm = FakeLLM([asks(tool_call("broken", '{"item_id": 1}')), says("never")])

    with pytest.raises(KeyError):
        await run(llm, registry)


async def test_step_limit_raises(registry):
    llm = FakeLLM([asks(tool_call("lookup", '{"item_id": 7}'))] * 3)

    with pytest.raises(AgentStepLimitError, match="within 3 steps"):
        await run(llm, registry, max_steps=3)
    assert len(llm.calls) == 3


async def test_answer_on_last_allowed_step_succeeds(registry):
    llm = FakeLLM(
        [
            asks(tool_call("lookup", '{"item_id": 6}')),
            asks(tool_call("lookup", '{"item_id": 7}')),
            says("Done."),
        ]
    )

    result = await run(llm, registry, max_steps=3)

    assert result.steps == 3


async def test_empty_final_reply_raises(registry):
    llm = FakeLLM([says("")])

    with pytest.raises(LLMOutputError, match="empty reply"):
        await run(llm, registry)
