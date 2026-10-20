from datetime import datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.actions import (
    ActionStatus,
    BookProposal,
    CancelProposal,
    PendingAction,
    Proposal,
    RescheduleProposal,
)

PROPOSAL = TypeAdapter(Proposal)
CREATED = datetime(2026, 10, 19, 9, 0)
EXPIRES = datetime(2026, 10, 19, 9, 10)


def make_action(**overrides):
    action = {
        "action_id": "a1",
        "conversation_id": "c1",
        "patient_id": "P1001",
        "proposal": {"kind": "cancel", "appointment_id": 1002},
        "status": "pending",
        "created_at": CREATED,
        "expires_at": EXPIRES,
    }
    return PendingAction.model_validate(action | overrides)


def test_proposals_default_their_kind():
    assert BookProposal(doctor_id="D01", starts_at=CREATED).kind == "book"
    assert CancelProposal(appointment_id=1).kind == "cancel"
    assert RescheduleProposal(appointment_id=1, starts_at=CREATED).kind == "reschedule"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {"kind": "book", "doctor_id": "D01", "starts_at": "2026-10-22T10:00"},
            BookProposal(doctor_id="D01", starts_at=datetime(2026, 10, 22, 10, 0)),
        ),
        (
            {"kind": "cancel", "appointment_id": 1002},
            CancelProposal(appointment_id=1002),
        ),
        (
            {
                "kind": "reschedule",
                "appointment_id": 1002,
                "starts_at": "2026-10-27T12:00",
            },
            RescheduleProposal(
                appointment_id=1002, starts_at=datetime(2026, 10, 27, 12, 0)
            ),
        ),
    ],
)
def test_kind_picks_the_proposal_model(data, expected):
    proposal = PROPOSAL.validate_python(data)

    assert type(proposal) is type(expected)
    assert proposal == expected


def test_kind_decides_even_when_fields_fit_another_model():
    proposal = PROPOSAL.validate_python(
        {"kind": "cancel", "appointment_id": 1002, "starts_at": "2026-10-27T12:00"}
    )

    assert type(proposal) is CancelProposal


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"appointment_id": 1002}, "Unable to extract tag"),
        ({"kind": "delete", "appointment_id": 1002}, "does not match any of"),
        ({"kind": "book", "doctor_id": "D01"}, "starts_at"),
        ({"kind": "reschedule", "appointment_id": 1002}, "starts_at"),
        ({"kind": "cancel"}, "appointment_id"),
        ({"kind": "cancel", "appointment_id": "soon"}, "appointment_id"),
    ],
)
def test_invalid_proposal_is_rejected(data, message):
    with pytest.raises(ValidationError, match=message):
        PROPOSAL.validate_python(data)


def test_proposal_round_trips_through_json():
    proposal = BookProposal(doctor_id="D01", starts_at=datetime(2026, 10, 22, 10, 0))

    assert PROPOSAL.validate_json(proposal.model_dump_json()) == proposal


def test_pending_action_parses_its_proposal():
    action = make_action()

    assert action.status is ActionStatus.PENDING
    assert action.proposal == CancelProposal(appointment_id=1002)
    assert action.result is None


def test_pending_action_rejects_an_unknown_status():
    with pytest.raises(ValidationError, match="status"):
        make_action(status="expired")


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 19, 9, 0), False),
        (datetime(2026, 10, 19, 9, 9, 59), False),
        (datetime(2026, 10, 19, 9, 10), True),
        (datetime(2026, 10, 20, 9, 0), True),
    ],
)
def test_pending_action_expires_at_its_deadline(now, expected):
    assert make_action().is_expired(now) is expected


@pytest.mark.parametrize("status", ["confirmed", "superseded"])
def test_only_pending_actions_can_expire(status):
    action = make_action(status=status)

    assert action.is_expired(datetime(2026, 10, 20, 9, 0)) is False
