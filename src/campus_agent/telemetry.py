"""Per-task model metadata, isolated by asyncio context; no prompts or secrets."""
from contextvars import ContextVar

MODEL_CALLS = ContextVar('campuspilot_model_calls', default=None)
