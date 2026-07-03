"""
schemas.py
----------
Pydantic models used across the app. These are the single source of truth
for what a valid request/response looks like — backend.py, wingman.py, and
app.py all import from here instead of redefining shapes.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    """What the frontend sends to the backend for one turn."""
    relationship: str = Field(..., description="Key into memory.json, e.g. 'Aisha'")
    message: str = Field(..., min_length=1, description="The user's situation/question")


class MemoryCandidate(BaseModel):
    """
    Something the model noticed in the message that might be worth
    remembering. Never written to memory.json automatically — surfaced to
    the UI for accept/discard, per Sparkeefy's 'no permanent memory without
    approval' rule.
    """
    category: str  # "preference" | "important_date" | "conversation_style"
    value: str

    @field_validator("category")
    @classmethod
    def known_category(cls, v):
        allowed = {"preference", "important_date", "conversation_style"}
        if v not in allowed:
            raise ValueError(f"category '{v}' not in {allowed}")
        return v


class WingmanResponse(BaseModel):
    """
    The exact structured output contract required by the assignment, plus
    memory_candidates (additive — doesn't break the required schema, just
    extends it for the chat-with-memory feature).
    Any DeepSeek output that doesn't validate against this is treated as a
    failure and surfaced as an error, not silently patched.
    """
    mode: str
    energy_read: str
    wingman_response: str
    suggested_messages: List[str] = Field(default_factory=list)
    follow_up_question: Optional[str] = None
    safety_flag: bool = False
    confidence: float
    memory_candidates: List[MemoryCandidate] = Field(default_factory=list)

    @field_validator("mode")
    @classmethod
    def mode_must_be_known(cls, v):
        allowed = {"reply_suggestion", "advice", "planning", "clarification_needed"}
        if v not in allowed:
            raise ValueError(f"mode '{v}' not in {allowed}")
        return v

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v):
        if not (0.0 <= v <= 1.0):
            raise ValueError("confidence must be between 0 and 1")
        return v

    @field_validator("suggested_messages")
    @classmethod
    def max_three_suggestions(cls, v):
        if len(v) > 3:
            raise ValueError("suggested_messages must contain at most 3 items")
        return v


class RelationshipMemory(BaseModel):
    """One relationship's stored context, loaded from memory.json."""
    name: str
    relationship_type: str = "unspecified"
    preferences: List[str] = Field(default_factory=list)
    important_dates: List[dict] = Field(default_factory=list)
    conversation_style: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    relationship_stage: Optional[str] = None
    updated_at: Optional[float] = None
