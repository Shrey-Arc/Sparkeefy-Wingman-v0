"""
memory.py
---------
Flat JSON memory store, per the assignment's actual scope: one file,
keyed by relationship name. No graph DB, no vector store — the assignment
doesn't require persistence beyond "the AI should feel like it knows this
person," and JSON does that fine for v0.

memory.json shape:
{
  "Aisha": {
    "name": "Aisha",
    "relationship_type": "girlfriend",
    "preferences": ["likes coffee", "favorite flower is sunflower"],
    "important_dates": [{"title": "Birthday", "date": "2005-03-12"}],
    "conversation_style": ["uses emojis", "short replies"]
  }
}
"""

import os
import sys
import time
from typing import Optional

from pydantic import ValidationError

from schemas import RelationshipMemory
import storage_utils as storage

MEMORY_PATH = os.path.join(os.path.dirname(__file__), "memory.json")
HISTORY_PATH = os.path.join(os.path.dirname(__file__), "conversations.json")
MAX_HISTORY_TURNS_IN_PROMPT = 10  # keep prompts cheap — recent context only


class MemoryManager:
    def __init__(self, path: str = MEMORY_PATH, history_path: str = HISTORY_PATH):
        self.path = path
        self.history_path = history_path

    def _load_all(self) -> dict:
        return storage.safe_read_json(self.path, {})

    def _save_all(self, data: dict):
        storage.atomic_write_json(self.path, data, indent=2, ensure_ascii=False)

    def list_relationships(self) -> list:
        return list(self._load_all().keys())

    def get(self, relationship: str) -> Optional[RelationshipMemory]:
        data = self._load_all()
        record = data.get(relationship)
        if record is None:
            return None
        try:
            return RelationshipMemory(**record)
        except ValidationError as e:
            # A malformed individual record (bad manual edit, a future schema
            # change, etc.) shouldn't crash the whole chat request — treat it
            # like a fresh profile and say so loudly, rather than silently
            # losing data or taking the app down.
            print(
                f"[memory.py] WARNING: stored record for '{relationship}' failed validation "
                f"({e}). Treating as a fresh profile for this request; the on-disk record is "
                f"left untouched in case it's recoverable.",
                file=sys.stderr,
            )
            return RelationshipMemory(name=relationship)

    def get_or_default(self, relationship: str) -> RelationshipMemory:
        """Never blocks a chat turn just because memory doesn't exist yet."""
        mem = self.get(relationship)
        if mem is None:
            return RelationshipMemory(name=relationship)
        return mem

    def upsert(self, relationship: str, memory: RelationshipMemory):
        memory.updated_at = time.time()
        def mutate(data):
            data[relationship] = memory.model_dump()
            return data
        storage.read_modify_write_json(self.path, {}, mutate, indent=2, ensure_ascii=False)

    # ---- Conversation history (makes chat feel continuous, Insta-DM style) ----

    def _load_history_all(self) -> dict:
        return storage.safe_read_json(self.history_path, {})

    def _save_history_all(self, data: dict):
        storage.atomic_write_json(self.history_path, data, indent=2, ensure_ascii=False)

    def get_history(self, relationship: str, limit: int = MAX_HISTORY_TURNS_IN_PROMPT) -> list:
        """Returns the last `limit` turns as [{"role": "user"|"assistant", "content": str}, ...]."""
        data = self._load_history_all()
        turns = data.get(relationship, [])
        return [{"role": t["role"], "content": t["content"]} for t in turns[-limit:]]

    def get_full_history(self, relationship: str) -> list:
        """Full history for rendering in the UI (includes timestamps)."""
        data = self._load_history_all()
        return data.get(relationship, [])

    def append_turn(self, relationship: str, role: str, content: str):
        def mutate(data):
            data.setdefault(relationship, [])
            data[relationship].append({"role": role, "content": content, "ts": time.time()})
            return data
        storage.read_modify_write_json(self.history_path, {}, mutate, indent=2, ensure_ascii=False)

    def clear_history(self, relationship: str):
        def mutate(data):
            data[relationship] = []
            return data
        storage.read_modify_write_json(self.history_path, {}, mutate, indent=2, ensure_ascii=False)

    # ---- Memory candidate approval (never auto-written — user decides) ----

    def approve_candidate(self, relationship: str, category: str, value: str):
        """
        Applies one approved MemoryCandidate to the relationship's permanent
        memory. Called only from an explicit user action (UI button), never
        automatically from the model's output.

        Holds the file lock across the read (get_or_default) AND the write
        (upsert) — without this, a concurrent request could read the same
        stale state between this method's read and its write, and one of
        the two approved candidates would silently disappear. storage.get_lock
        returns a reentrant lock, so upsert()'s own internal locking on the
        same path nests safely inside this outer lock from the same thread.
        """
        if category not in {"preference", "conversation_style", "important_date"}:
            raise ValueError(f"Unknown category: {category}")

        with storage.get_lock(self.path):
            mem = self.get_or_default(relationship)
            if category == "preference":
                if value not in mem.preferences:
                    mem.preferences.append(value)
            elif category == "conversation_style":
                if value not in mem.conversation_style:
                    mem.conversation_style.append(value)
            elif category == "important_date":
                # value expected as "Title: YYYY-MM-DD" or free text; store as-is,
                # title-only dict if it doesn't parse cleanly.
                if ":" in value:
                    title, _, date = value.partition(":")
                    mem.important_dates.append({"title": title.strip(), "date": date.strip()})
                else:
                    mem.important_dates.append({"title": value.strip(), "date": ""})
            self.upsert(relationship, mem)

    def as_context_string(self, relationship: str) -> str:
        """
        Renders memory as a short natural-language block for the prompt.
        Deliberately terse — this is Context Builder territory: only what's
        useful, nothing that pads tokens for no reason.
        """
        mem = self.get_or_default(relationship)
        if not any([mem.preferences, mem.important_dates, mem.conversation_style, mem.notes]):
            return f"No stored memory yet for {relationship}. Treat as a fresh context."

        stage = f", stage: {mem.relationship_stage}" if mem.relationship_stage else ""
        lines = [f"Relationship: {relationship} ({mem.relationship_type}{stage})"]
        if mem.preferences:
            lines.append("Known preferences: " + "; ".join(mem.preferences))
        if mem.important_dates:
            dates = "; ".join(f"{d.get('title')}: {d.get('date')}" for d in mem.important_dates)
            lines.append("Important dates: " + dates)
        if mem.conversation_style:
            lines.append("Communication style: " + "; ".join(mem.conversation_style))
        if mem.notes:
            lines.append("Notes: " + "; ".join(mem.notes))
        return "\n".join(lines)

    def sidebar_summary(self, relationship: Optional[str]) -> dict:
        """
        Human-readable snapshot for the Streamlit 'context window' panel:
        the exact memory block the model would see, plus a relative
        last-updated time. Returns an empty snapshot if no relationship is
        selected, so the sidebar has something explicit to clear against.
        """
        if not relationship:
            return {"relationship": None, "context_text": "", "last_updated_human": None, "has_memory": False}

        mem = self.get_or_default(relationship)
        has_memory = any([mem.preferences, mem.important_dates, mem.conversation_style, mem.notes])
        return {
            "relationship": relationship,
            "context_text": self.as_context_string(relationship),
            "last_updated_human": _humanize_age(mem.updated_at) if mem.updated_at else "never updated",
            "has_memory": has_memory,
        }


def _humanize_age(ts: float) -> str:
    delta = max(0, time.time() - ts)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)} hr ago"
    return f"{int(delta // 86400)} day(s) ago"
