import json
import os

from openai import OpenAI

MODEL = "openai/gpt-oss-120b"

client = OpenAI(
    api_key=os.environ["LLM_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
)

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_local_time",
            "description": "Get the current local time in a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]


def get_local_time(city: str) -> dict:
    return {"city": city, "time": "14:30"}


messages = [{"role": "user", "content": "What time is it in Cairo right now?"}]

first = client.chat.completions.create(model=MODEL, messages=messages, tools=tools)
request = first.choices[0].message
print(first.choices[0].finish_reason)
print(request.model_dump_json(indent=2, exclude_none=True))

messages.append(request.model_dump(exclude_none=True))
for call in request.tool_calls or []:
    result = get_local_time(**json.loads(call.function.arguments))
    messages.append(
        {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}
    )

final = client.chat.completions.create(model=MODEL, messages=messages, tools=tools)
print(final.choices[0].message.content)
