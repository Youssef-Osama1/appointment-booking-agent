import json
from datetime import date, datetime, time
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from app.config.settings import Settings
from app.core.exceptions import ToolError
from app.repositories.action_repository import ActionRepository
from app.repositories.appointment_repository import (
    AppointmentRepository,
    initialize_database,
)
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.database import Database
from app.schemas.actions import (
    ActionStatus,
    BookProposal,
    CancelProposal,
    RescheduleProposal,
)
from app.schemas.clinic import Specialty
from app.services.action_service import ActionService
from app.tools import booking
from app.tools.booking import (
    FindSlotsArgs,
    PartOfDay,
    PatientTools,
    ProposeBookingArgs,
    resolve_day,
)

CLINIC_PATH = Path(__file__).resolve().parents[2] / "data" / "clinic.json"
NOW = datetime(2026, 10, 19, 9, 0)
MONDAY = date(2026, 10, 19)
ARGS_MODELS = [
    model
    for model in vars(booking).values()
    if isinstance(model, type)
    and issubclass(model, BaseModel)
    and model.__name__.endswith("Args")
]


@pytest.fixture
def clinic():
    return ClinicRepository.from_file(CLINIC_PATH)


@pytest.fixture
def database(tmp_path, clinic):
    database = Database(tmp_path / "booking.db")
    initialize_database(database, clinic.seed_appointments)
    return database


@pytest.fixture
def make_tools(clinic, database):
    def make(patient_id="P1001", now=NOW):
        settings = Settings(_env_file=None, llm_api_key="k", now_override=now)
        with database.connect() as connection:
            conversation_id = ConversationRepository(connection).create(patient_id, now)
        return PatientTools(
            clinic,
            database,
            ActionService(database, clinic, settings),
            clinic.find_patient(patient_id),
            conversation_id,
            now,
        )

    return make


@pytest.fixture
def tools(make_tools):
    return make_tools()


def appointment_rows(database):
    with database.connect() as connection:
        rows = connection.execute("SELECT * FROM appointments ORDER BY 1")
        return [tuple(row) for row in rows]


def pending(database, tools):
    with database.connect() as connection:
        return ActionRepository(connection).find_pending(tools._conversation_id)


def free_times(result, doctor_id):
    return next(
        d["free_times"] for d in result["doctors"] if d["doctor_id"] == doctor_id
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("today", MONDAY),
        ("Today", MONDAY),
        (" tomorrow ", date(2026, 10, 20)),
        ("monday", MONDAY),
        ("thursday", date(2026, 10, 22)),
        ("THURSDAY", date(2026, 10, 22)),
        ("friday", date(2026, 10, 23)),
        ("sunday", date(2026, 10, 25)),
        ("2026-10-19", MONDAY),
        ("2026-11-03", date(2026, 11, 3)),
    ],
)
def test_resolve_day(text, expected):
    assert resolve_day(text, MONDAY) == expected


def test_resolve_day_wraps_to_next_week():
    thursday = date(2026, 10, 22)

    assert resolve_day("monday", thursday) == date(2026, 10, 26)
    assert resolve_day("thursday", thursday) == thursday


@pytest.mark.parametrize("text", ["yesterday", "next thursday", "الخميس", "22/10"])
def test_resolve_day_rejects_unknown_words(text):
    with pytest.raises(ToolError, match="Unknown day"):
        resolve_day(text, MONDAY)


def test_resolve_day_rejects_past_dates():
    with pytest.raises(ToolError, match="2026-10-18 is in the past"):
        resolve_day("2026-10-18", MONDAY)


@pytest.mark.parametrize(
    ("part", "moment", "expected"),
    [
        (PartOfDay.MORNING, time(11, 30), True),
        (PartOfDay.MORNING, time(12, 0), False),
        (PartOfDay.AFTERNOON, time(12, 0), True),
        (PartOfDay.AFTERNOON, time(16, 30), True),
        (PartOfDay.AFTERNOON, time(17, 0), False),
        (PartOfDay.EVENING, time(17, 0), True),
        (PartOfDay.EVENING, time(16, 30), False),
        (PartOfDay.ANY, time(0, 0), True),
    ],
)
def test_part_of_day_includes(part, moment, expected):
    assert part.includes(moment) is expected


@pytest.mark.parametrize("model", ARGS_MODELS, ids=lambda model: model.__name__)
def test_no_tool_takes_a_patient_or_conversation(model):
    properties = model.model_json_schema().get("properties", {})

    assert not {"patient_id", "patient", "conversation_id"} & set(properties)


def test_every_tool_has_an_args_model():
    assert len(ARGS_MODELS) == 6


def test_args_normalize_doctor_id():
    args = ProposeBookingArgs.model_validate_json(
        '{"doctor_id": " d01 ", "day": "Thursday", "start": " 10:00 "}'
    )

    assert args.doctor_id == "D01"
    assert args.start == "10:00"


@pytest.mark.parametrize(
    "payload",
    [
        '{"doctor_id": "D01", "day": "", "start": "10:00"}',
        '{"doctor_id": "D01", "start": "10:00"}',
        '{"doctor_id": "D01", "day": "thursday", "start": ""}',
        '{"doctor_id": "D01", "day": "thursday", "start": 10}',
    ],
)
def test_bad_booking_args_are_rejected(payload):
    with pytest.raises(ValidationError):
        ProposeBookingArgs.model_validate_json(payload)


def test_find_slots_schema_names_allowed_values_in_descriptions():
    schema = json.dumps(FindSlotsArgs.model_json_schema())

    assert "dentistry, dermatology, pediatrics or general" in schema
    assert "morning" in schema


@pytest.mark.parametrize("model", ARGS_MODELS, ids=lambda model: model.__name__)
def test_schemas_leave_strict_checks_to_python(model):
    schema = json.dumps(model.model_json_schema())

    assert '"enum"' not in schema
    assert '"format"' not in schema
    assert '"pattern"' not in schema


def test_list_doctors(tools):
    result = tools.list_doctors()

    assert [d["doctor_id"] for d in result["doctors"]] == [
        "D01",
        "D02",
        "D03",
        "D04",
        "D05",
    ]
    assert result["doctors"][0] == {
        "doctor_id": "D01",
        "name": "Dr. Karim Adel",
        "specialty": "dentistry",
        "working_hours": [
            "saturday 09:00-13:00",
            "monday 09:00-13:00",
            "thursday 09:00-13:00",
        ],
    }


def test_list_doctors_by_specialty(tools):
    result = tools.list_doctors(Specialty.DENTISTRY)

    assert [d["name"] for d in result["doctors"]] == [
        "Dr. Karim Adel",
        "Dr. Mona Hassan",
    ]


def test_find_slots_hides_booked_and_names_no_one(tools):
    result = tools.find_slots("thursday", specialty=Specialty.DENTISTRY)

    assert result["date"] == "2026-10-22"
    assert result["day"] == "thursday"
    assert free_times(result, "D01") == [
        "09:00",
        "09:30",
        "10:00",
        "11:30",
        "12:00",
        "12:30",
    ]
    text = json.dumps(result)
    assert "P1002" not in text
    assert "Nour" not in text
    assert "Hana" not in text


def test_find_slots_filters_part_of_day(tools):
    result = tools.find_slots(
        "thursday", specialty=Specialty.DENTISTRY, part_of_day=PartOfDay.MORNING
    )

    assert free_times(result, "D01") == ["09:00", "09:30", "10:00", "11:30"]
    assert free_times(result, "D02") == []
    assert result["part_of_day"] == "morning"


def test_find_slots_evening(tools):
    result = tools.find_slots(
        "thursday", specialty=Specialty.DENTISTRY, part_of_day=PartOfDay.EVENING
    )

    assert free_times(result, "D01") == []
    assert "17:00" not in free_times(result, "D02")
    assert free_times(result, "D02")[:2] == ["17:30", "18:00"]


def test_find_slots_one_doctor(tools):
    result = tools.find_slots("wednesday", doctor_id="D04")

    assert [d["doctor_id"] for d in result["doctors"]] == ["D04"]
    times = free_times(result, "D04")
    assert "09:30" not in times
    assert times[:2] == ["09:00", "10:00"]
    assert "17:00" in times
    assert "12:00" not in times


def test_find_slots_skips_doctors_off_that_day(tools):
    result = tools.find_slots("friday", specialty=Specialty.DENTISTRY)

    assert result["day"] == "friday"
    assert result["doctors"] == []


def test_find_slots_skips_times_that_passed(make_tools):
    tools = make_tools(now=datetime(2026, 10, 22, 10, 5))

    result = tools.find_slots("today", doctor_id="D01")

    assert free_times(result, "D01") == ["11:30", "12:00", "12:30"]


def test_find_slots_shows_a_cancelled_slot_as_free(tools):
    result = tools.find_slots("tuesday", doctor_id="D05")

    assert "11:00" in free_times(result, "D05")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"day": "thursday"}, "Give a specialty or a doctor_id"),
        ({"day": "thursday", "doctor_id": "D99"}, "Unknown doctor D99"),
        ({"day": "yesterday", "doctor_id": "D01"}, "Unknown day"),
        ({"day": "2026-10-01", "doctor_id": "D01"}, "in the past"),
    ],
)
def test_find_slots_errors(tools, kwargs, message):
    with pytest.raises(ToolError, match=message):
        tools.find_slots(**kwargs)


def test_get_my_appointments_shows_upcoming_only(tools):
    result = tools.get_my_appointments()

    assert result == {
        "name": "Omar Khaled",
        "appointments": [
            {
                "appointment_id": 1002,
                "doctor_id": "D03",
                "doctor": "Dr. Ahmed Farouk",
                "specialty": "dermatology",
                "date": "2026-10-24",
                "day": "saturday",
                "time": "11:00",
            }
        ],
    }


def test_get_my_appointments_never_shows_other_patients(make_tools):
    result = make_tools("P1002").get_my_appointments()

    ids = [a["appointment_id"] for a in result["appointments"]]
    assert ids == [1003, 1004]
    assert "Omar" not in json.dumps(result)


def test_propose_booking_stores_a_pending_action_only(tools, database):
    before = appointment_rows(database)

    result = tools.propose_booking("D01", "thursday", "10:00")

    assert result == {
        "status": "awaiting_confirmation",
        "action": "book",
        "note": "Nothing has changed yet. The patient must press Confirm.",
        "doctor_id": "D01",
        "doctor": "Dr. Karim Adel",
        "date": "2026-10-22",
        "day": "thursday",
        "time": "10:00",
    }
    assert appointment_rows(database) == before
    action = pending(database, tools)
    assert action.patient_id == "P1001"
    assert action.proposal == BookProposal(
        doctor_id="D01", starts_at=datetime(2026, 10, 22, 10, 0)
    )


def test_propose_result_hides_the_action_id(tools, database):
    result = tools.propose_booking("D01", "thursday", "10:00")

    assert pending(database, tools).action_id not in json.dumps(result)


@pytest.mark.parametrize("start", ["10:00", " 10:00 ", "10:00:00", "10:00+02:00"])
def test_propose_booking_reads_the_start_time(tools, database, start):
    tools.propose_booking("D01", "thursday", start)

    starts_at = pending(database, tools).proposal.starts_at
    assert starts_at == datetime(2026, 10, 22, 10, 0)
    assert starts_at.tzinfo is None


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("D01", "thursday", "10:30"), "already booked"),
        (("D01", "thursday", "08:00"), "no appointment slot"),
        (("D01", "thursday", "10:15"), "no appointment slot"),
        (("D01", "friday", "10:00"), "no appointment slot"),
        (("D99", "thursday", "10:00"), "Unknown doctor D99"),
        (("D01", "today", "09:00"), "already passed"),
        (("D01", "thursday", "10am"), "Unknown time '10am'; use 24-hour HH:MM"),
        (("D01", "thursday", "9:00"), "Unknown time"),
        (("D01", "thursday", "25:00"), "Unknown time"),
        (("D03", "saturday", "11:00"), "already booked"),
    ],
)
def test_propose_booking_errors_reach_the_model(tools, database, args, message):
    with pytest.raises(ToolError, match=message):
        tools.propose_booking(*args)

    assert pending(database, tools) is None


def test_propose_cancellation(tools, database):
    before = appointment_rows(database)

    result = tools.propose_cancellation(1002)

    assert result["status"] == "awaiting_confirmation"
    assert result["action"] == "cancel"
    assert result["appointment_id"] == 1002
    assert result["doctor"] == "Dr. Ahmed Farouk"
    assert result["date"] == "2026-10-24"
    assert appointment_rows(database) == before
    assert pending(database, tools).proposal == CancelProposal(appointment_id=1002)


@pytest.mark.parametrize("appointment_id", [1003, 1004, 1005, 1001, 1006, 9999])
def test_propose_cancellation_cannot_touch_others(tools, database, appointment_id):
    with pytest.raises(ToolError) as error:
        tools.propose_cancellation(appointment_id)

    assert str(error.value) == (
        f"Appointment {appointment_id} is not one of your upcoming appointments"
    )
    assert pending(database, tools) is None


def test_propose_reschedule(tools, database):
    before = appointment_rows(database)

    result = tools.propose_reschedule(1002, "tuesday", "12:00")

    assert result["action"] == "reschedule"
    assert result["appointment_id"] == 1002
    assert (result["date"], result["time"]) == ("2026-10-24", "11:00")
    assert result["new"] == {"date": "2026-10-20", "day": "tuesday", "time": "12:00"}
    assert appointment_rows(database) == before
    assert pending(database, tools).proposal == RescheduleProposal(
        appointment_id=1002, starts_at=datetime(2026, 10, 20, 12, 0)
    )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((1003, "thursday", "09:00"), "not one of your upcoming"),
        ((1002, "saturday", "11:00"), "already at that time"),
        ((1002, "thursday", "10:00"), "Dr. Ahmed Farouk has no appointment slot"),
        ((1002, "someday", "10:00"), "Unknown day"),
    ],
)
def test_propose_reschedule_errors(tools, database, args, message):
    with pytest.raises(ToolError, match=message):
        tools.propose_reschedule(*args)

    assert pending(database, tools) is None


def test_new_proposal_replaces_the_previous_one(tools, database):
    tools.propose_booking("D01", "thursday", "10:00")
    first = pending(database, tools)

    tools.propose_booking("D01", "thursday", "11:30")

    with database.connect() as connection:
        old = ActionRepository(connection).find(first.action_id, "P1001")
    assert old.status is ActionStatus.SUPERSEDED
    assert pending(database, tools).proposal.starts_at == datetime(2026, 10, 22, 11, 30)


def test_tool_results_are_json_ready(tools):
    results = [
        tools.list_doctors(),
        tools.find_slots("thursday", specialty=Specialty.DENTISTRY),
        tools.get_my_appointments(),
        tools.propose_booking("D01", "thursday", "10:00"),
        tools.propose_reschedule(1002, "tuesday", "12:00"),
        tools.propose_cancellation(1002),
    ]

    for result in results:
        json.dumps(result)


def test_tools_book_nothing_until_confirm(tools, database):
    tools.propose_booking("D01", "thursday", "10:00")
    with database.connect() as connection:
        assert not AppointmentRepository(connection).is_doctor_booked(
            "D01", datetime(2026, 10, 22, 10, 0)
        )


@pytest.mark.parametrize("value", ["dentistry", "Dentistry", " DENTISTRY "])
def test_specialty_is_matched_in_python(tools, value):
    result = tools.find_slots("thursday", specialty=value)

    assert [d["doctor_id"] for d in result["doctors"]] == ["D01", "D02"]
    assert len(tools.list_doctors(value)["doctors"]) == 2


@pytest.mark.parametrize("value", ["cardiology", "أسنان", "dentist"])
def test_unknown_specialty_goes_back_to_the_model(tools, value):
    message = "use dentistry, dermatology, pediatrics or general"

    with pytest.raises(ToolError, match=message):
        tools.find_slots("thursday", specialty=value)
    with pytest.raises(ToolError, match=message):
        tools.list_doctors(value)


@pytest.mark.parametrize("value", ["Morning", " EVENING ", "any"])
def test_part_of_day_is_matched_in_python(tools, value):
    result = tools.find_slots("thursday", doctor_id="D02", part_of_day=value)

    assert result["part_of_day"] == value.strip().lower()


def test_unknown_part_of_day_goes_back_to_the_model(tools):
    with pytest.raises(ToolError, match="Unknown part of day 'noon'"):
        tools.find_slots("thursday", doctor_id="D01", part_of_day="noon")
