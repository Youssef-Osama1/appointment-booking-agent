# Appointment Booking Agent

[![CI](https://github.com/Youssef-Osama1/appointment-booking-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Youssef-Osama1/appointment-booking-agent/actions/workflows/ci.yml)

A clinic booking agent that finds free times, books, cancels and reschedules appointments, where the model can only propose a change and the patient confirms it.

Send a patient message and the agent calls tools over the clinic's doctors, schedules and
bookings, then replies in the patient's language. Conversations are stored on the server,
so a follow-up such as "book the 10:00 one" knows what came before.

## How It Works

1. **Ask**: the model gets the conversation and six tools: `list_doctors`, `find_slots`,
   `get_my_appointments`, `propose_booking`, `propose_cancellation` and `propose_reschedule`.
2. **Propose**: a `propose_*` tool checks the request in Python and stores it as a pending
   action. Nothing changes yet, and the reply carries the pending action from the database.
3. **Confirm**: the patient calls the confirm endpoint. Python checks the request again,
   applies it and writes a note into the conversation. The model is not involved.

Safety rules:

- **The model never writes.** No tool books anything, and there is no confirm tool. Only the
  confirm endpoint changes appointments.
- **Identity comes from the request.** The patient ID is bound to the tools in Python. No tool
  takes a patient argument, and another patient's appointment looks the same as one that does
  not exist.
- **The check that counts runs at confirm time.** Confirm runs inside `BEGIN IMMEDIATE`, and
  partial unique indexes allow one booking per doctor slot and per patient slot. When two
  patients confirm the same time, one gets 200 and the other 409.
- **Confirm is idempotent.** Confirming the same action again returns the stored result and
  books nothing new.
- **One proposal waits at a time.** A new proposal in the conversation replaces the last one,
  and a proposal expires after `PENDING_ACTION_TTL_MINUTES`.
- **Python does the math.** Weekday names, morning or evening, free slots and past times are
  worked out in the tools, not by the model.

Tool arguments are plain strings in the schema and are checked in Python, so a mistake such
as `"Dentistry"` goes back to the model as a tool error it can fix, instead of being rejected
by the provider and failing the request.

The clock follows `CLINIC_TIMEZONE`. Set `NOW_OVERRIDE=2026-10-19T09:00` in `.env` to pin it.

## Quick Start

```bash
uv sync
cp .env.example .env    # add your LLM_API_KEY
uv run uvicorn app.main:app --reload
```

API docs at http://localhost:8000/docs. The SQLite database is created at `data/booking.db`
and seeded from `data/clinic.json` on first start.

Chat with `POST /chat` and confirm a pending action with `POST /actions/{action_id}/confirm`.

## Docker

```bash
docker build -f docker/Dockerfile -t appointment-booking-agent .
docker run --rm -p 8000:8000 --env-file .env appointment-booking-agent
```

The image installs runtime dependencies only, includes `data/clinic.json`, runs as a
non-root user and listens on `$PORT` (default 8000). The local `data/booking.db` is never
copied into the image: the container creates and seeds its own database in `data/`, which
is the only folder the app user can write to. `.env` is never copied into the image, so
secrets are passed at run time. Values in `.env` must not be quoted for `--env-file`.

## CI and Deployment

Every push to `main` and every pull request runs `.github/workflows/ci.yml`: ruff lint,
ruff format check, pytest and a Docker build.

The app runs on Render as a Docker web service:

| Setting | Value |
|---|---|
| Dockerfile path | `./docker/Dockerfile` |
| Build context | `.` |
| Health check path | `/health` |
| Auto-deploy | After CI checks pass |
| Environment | `LLM_API_KEY`, `NOW_OVERRIDE` (other settings have defaults) |

`NOW_OVERRIDE=2026-10-19T09:00` fixes the clinic clock, so free slots and the seeded
appointments line up. Without it, the real time is used and the sample appointments end
up in the past.

The free instance has no persistent disk. The database is reset to the seed data on every
deploy, restart and spin-down, so bookings and conversations do not survive them.

## Project Structure

```text
├── .github/          # CI workflow
├── app/
│   ├── api/          # Routes, dependencies and request/response models
│   ├── config/       # Settings loaded from .env
│   ├── core/         # Logging and exceptions
│   ├── prompts/      # System prompt and tool descriptions
│   ├── repositories/ # SQLite access for appointments, actions and conversations
│   ├── schemas/      # Clinic and pending action models
│   ├── services/     # LLM client, agent loop, booking rules and services
│   ├── tools/        # Patient tools
│   └── main.py       # FastAPI app entry point
├── data/             # Sample doctors, schedules, patients and appointments
├── docker/           # Dockerfile
├── docs/             # Documentation files
├── evals/            # Eval cases and runner
├── playground/       # Experimental code, never imported by app/
├── tests/            # Tests that run without network access
├── .env.example      # Environment variables template
├── pyproject.toml    # Project dependencies and metadata
└── uv.lock           # Locked dependencies
```
