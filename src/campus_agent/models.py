from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Evidence(StrictModel):
    evidence_id: str
    text: str
    source: str
    title: str
    campus_id: str
    valid_from: str = '2026-01-01'
    valid_to: str = '2026-12-31'
    version: str = 'demo-2026.1'
    score: float = 0
    backend: str = 'memory'
    school_id: str = 'synthetic'
    student_type: str = 'undergraduate'
    cohort_year: int = 2026
    source_url: str | None = None
    snapshot_sha256: str | None = None
    fetched_at: str | None = None
    source_notes: list[str] = Field(default_factory=list)


class PlanRequest(StrictModel):
    user_id: str = 'demo-user'
    session_id: str
    request: str
    campus_id: Literal['mingde', 'zhiyuan', 'whut', 'mafangshan', 'yujiatou', 'nanhu'] | None = None
    student_type: Literal['undergraduate', 'graduate'] | None = None
    cohort_year: int | None = Field(default=None, ge=2000, le=2100)
    funding_route: Literal['ordinary', 'origin_loan', 'campus_loan', 'unknown'] | None = None
    reference_date: str | None = None
    arrival: datetime | None = None
    service_ids: list[str] = Field(default_factory=lambda: ['payment', 'registration', 'dorm', 'card'], max_length=20)
    completed: list[str] = Field(default_factory=list)
    available_windows: list[tuple[datetime, datetime]] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    plan_id: str | None = None
    version: int = Field(default=1, ge=1)
    reminders: list[PersonalReminder] = Field(default_factory=list, max_length=20)

    @field_validator('arrival')
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError('arrival must contain timezone, e.g. +08:00')
        return value

    @field_validator('available_windows')
    @classmethod
    def windows_valid(cls, values):
        for start, end in values:
            if not start.tzinfo or not end.tzinfo or start >= end:
                raise ValueError('available windows require timezone and start < end')
        return values


class PersonalReminder(StrictModel):
    service_id: str
    start: datetime
    end: datetime

    @field_validator('end')
    @classmethod
    def reminder_end(cls, value, info):
        start = info.data.get('start')
        if not value.tzinfo or not start or not start.tzinfo or value <= start:
            raise ValueError('reminder requires timezone and start < end')
        return value


class PlanItem(StrictModel):
    item_id: str
    service_id: str
    title: str
    location: str
    location_name: str = ''
    start: datetime | None = None
    end: datetime | None = None
    kind: Literal['appointment', 'checklist', 'personal_reminder'] = 'appointment'
    due_date: str | None = None
    timing_note: str = ''
    dependencies: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)


class Plan(StrictModel):
    plan_id: str
    version: int = Field(ge=1)
    user_id: str
    session_id: str
    timezone: str = 'Asia/Shanghai'
    campus_id: str
    conditions: dict
    assumptions: list[str] = Field(default_factory=list)
    items: list[PlanItem] = Field(max_length=20)
    unarranged: list[dict] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    explanation: str = ''
    validated: bool = True
    data_profile: str = 'synthetic'


class SearchArgs(StrictModel):
    query: str = Field(min_length=1, max_length=2000)
    campus_id: Literal['mingde', 'zhiyuan', 'whut', 'mafangshan', 'yujiatou', 'nanhu'] | None = None
    as_of_date: str
    top_k: int = Field(default=5, ge=1, le=10)
    school_id: str | None = None
    student_type: Literal['undergraduate', 'graduate'] = 'undergraduate'
    cohort_year: int = Field(default=2026, ge=2000, le=2100)


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=5000)
    session_id: str | None = None


def success(data=None):
    return {'ok': True, 'data': data, 'error': None}


def failure(code: str, message: str):
    return {'ok': False, 'data': None, 'error': {'code': code, 'message': message}}


PlanRequest.model_rebuild()
