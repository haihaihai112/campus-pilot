from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


Route = Literal["faq", "knowledge", "structured", "chat"]


@dataclass(frozen=True)
class ParentChunk:
    id: str
    text: str
    source: str
    title: str
    category: str


@dataclass(frozen=True)
class ChildChunk:
    id: str
    text: str
    parent: ParentChunk


@dataclass(frozen=True)
class SearchHit:
    chunk: ParentChunk
    score: float
    dense_score: float = 0.0
    sparse_score: float = 0.0


class AgentState(TypedDict, total=False):
    session_id: str
    question: str
    route: Route
    faq_answer: str
    faq_score: float
    faq_question: str
    rewritten_query: str
    attempts: int
    contexts: list[str]
    hits: list[SearchHit]
    evidence_score: float
    answer: str
    citations: list[str]
    requires_human: bool
    trace: list[dict[str, Any]]


@dataclass
class AgentResult:
    session_id: str
    question: str
    route: str
    answer: str
    citations: list[str]
    contexts: list[str]
    requires_human: bool
    trace: list[dict[str, Any]]
    latency_ms: float
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    session_id: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    session_id: str
    question: str
    route: str
    answer: str
    citations: list[str]
    contexts: list[str]
    requires_human: bool
    trace: list[dict[str, Any]]
    latency_ms: float
    usage: dict[str, int]


class FeedbackRequest(BaseModel):
    session_id: str
    question: str
    answer: str
    helpful: bool
    comment: str = Field(default="", max_length=1000)
