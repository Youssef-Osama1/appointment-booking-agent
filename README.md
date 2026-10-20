# AI Agent Template

A production-ready Python starting point for tool-calling LLM agents.

## Quick Start

```bash
uv sync
cp .env.example .env    # add your LLM_API_KEY
uv run uvicorn app.main:app --reload
```

API docs at http://localhost:8000/docs.

## How the Agent Works

`run_agent` in `app/services/agent.py` runs the loop:

1. Send the conversation and the tool schemas to the model with `LLMClient.chat`.
2. If the model asks for tools, `ToolRegistry` validates the arguments with Pydantic,
   runs each tool and sends the results back.
3. Repeat until the model answers without a tool call, or stop at `AGENT_MAX_STEPS`.

Unknown tools, invalid arguments and `ToolError` go back to the model as error results,
so it can recover. Any other exception is a bug and is raised.

| Error | Status |
|-------|--------|
| `LLMOutputError` | 502, the model returned output that does not match the schema or an invalid tool call |
| `AgentStepLimitError` | 502, the agent did not finish within `AGENT_MAX_STEPS` |

## Project Structure

```text
├── app/
│   ├── api/          # Routes, dependencies and request/response models
│   ├── config/       # Settings loaded from .env
│   ├── core/         # Logging and exceptions
│   ├── prompts/      # System prompts
│   ├── repositories/ # Data access for the tools
│   ├── schemas/      # Pydantic models for the use case
│   ├── services/     # LLM client, agent loop and business logic
│   ├── tools/        # Tools the agent can call
│   └── main.py       # FastAPI app entry point
├── data/             # Local data the repositories load
├── docker/           # Docker configurations
├── docs/             # Documentation files
├── evals/            # Datasets for measuring agent behavior
├── playground/       # Experimental code, never imported by app/
├── tests/            # Tests that run without network access
├── .env.example      # Environment variables template
├── pyproject.toml    # Project dependencies and metadata
└── uv.lock           # Locked dependencies
```

## Adding an Agent Use Case

1. Put the data in `data/` and a repository that loads it once in `app/repositories/`.
2. Write each tool in `app/tools/`: a Pydantic model for its arguments and a function
   that returns a small dict. Raise `ToolError` for expected failures such as "not found".
3. In a service in `app/services/`, build a `ToolRegistry` from the tools and call
   `run_agent` with a system prompt from `app/prompts/`.
4. Expose the service through an endpoint in `app/api/endpoints/`.

Three rules keep the agent safe and predictable:

- **Identity comes from the request, not the model.** Bind the user to the tools in Python
  and let the model pass only IDs. The tool checks ownership itself.
- **Python does the math.** Dates, totals and eligibility are computed inside tools,
  never by the model.
- **Tools are read-only** unless an action is gated behind a confirmation step.
