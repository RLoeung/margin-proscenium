"""Process-local literary state and a versioned serialization boundary.

No storage, automatic scope detection, or synthesis lives here. An immutable
offline book prior may be attached separately from serialized runtime state.
"""
from collections import deque
import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

if __package__:
    from .book_preflight import BookPreflight
else:
    from book_preflight import BookPreflight


CONTEXT_LENGTH = 16
SCOPE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"


class StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(StateModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    kind: Literal["explicit-name", "explicit-reference", "quote-span-continuation",
                  "preceding-action", "following-action", "two-person-alternation"]
    scope_id: str = Field(min_length=1, pattern=SCOPE_PATTERN)
    position: int = Field(ge=1)
    text: str
    candidate: str | None = None
    strength: Literal["definitive", "suggestive"]


class SpeakerDecision(StateModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    status: Literal["narration", "resolved", "tentative", "unresolved"]
    speaker: str | None = None
    reason: str
    confidence: Literal["high", "medium", "low"]
    evidence: tuple[Evidence, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def check_identity(self):
        if (self.status in ("resolved", "tentative")) != (self.speaker is not None):
            raise ValueError("Only resolved/tentative decisions have a speaker")
        if self.speaker and self.speaker.startswith("__"):
            raise ValueError("Performance roles and fallback placeholders are not speakers")
        return self


class PerformanceDecision(StateModel):
    """Rendering choice only; never an input to literary inference."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    speaker: str | None = None
    reason: str
    confidence: Literal["high", "medium", "low"]


class Classification(StateModel):
    type: str
    confidence: Literal["high", "medium", "low"]
    has_attribution: bool
    speaker_name: str | None
    speaker_reference: str | None
    was_open: bool
    is_dialogue: bool
    quote_transition: str
    quote_open_after: bool
    curly_open_count: int = Field(ge=0)
    curly_close_count: int = Field(ge=0)
    straight_count: int = Field(ge=0)


class ContextEntry(StateModel):
    scope_id: str = Field(min_length=1, pattern=SCOPE_PATTERN)
    position: int = Field(ge=1)
    text: str
    classification: Classification
    narrative_actor: str | None
    decision: SpeakerDecision
    performance_decision: PerformanceDecision
    selected_voice: str
    cast_gender: Literal["female", "male"] | None
    voice_status: Literal["locked", "provisional", "pending_recast", "narrator"]
    refinement: SpeakerDecision | None = None
    following_evidence: tuple[Evidence, ...] = Field(default=(), max_length=1)

    @model_validator(mode="after")
    def check_decision_role(self):
        if self.classification.is_dialogue == (self.decision.status == "narration"):
            raise ValueError("Narration and dialogue decisions must match classification")
        if self.refinement and (not self.classification.is_dialogue or self.refinement.status == "narration"):
            raise ValueError("Only dialogue may carry a speaker refinement")
        return self

    def public_record(self):
        """Keep literary diagnostics separate from the original rendering choice."""
        return {
            "text": self.text, **self.classification.model_dump(),
            "scope_id": self.scope_id, "position": self.position,
            "narrative_actor": self.narrative_actor, "cast_speaker": self.decision.speaker,
            "cast_gender": self.cast_gender, "selected_voice": self.selected_voice,
            "routing_reason": self.decision.reason,
            "speaker_confidence": self.decision.confidence, "voice_status": self.voice_status,
            "decision": self.decision.model_dump(mode="json"),
            "performance_decision": self.performance_decision.model_dump(mode="json"),
            "refinement": self.refinement.model_dump(mode="json") if self.refinement else None,
            "following_evidence": [e.model_dump(mode="json") for e in self.following_evidence],
        }


class SceneState(StateModel):
    gender: dict[str, Literal["female", "male"]] = Field(default_factory=dict)
    recent_entities: deque[str] = Field(default_factory=lambda: deque(maxlen=8), max_length=8)
    pronouns: dict[Literal["she", "he"], str | None] = Field(default_factory=lambda: {"she": None, "he": None})
    participants: deque[str] = Field(default_factory=lambda: deque(maxlen=4), max_length=4)
    last_speaker: str | None = None
    previous_speaker: str | None = None
    quote_open: bool = False
    active_decision: SpeakerDecision | None = None
    pov_entity_id: str | None = None

    @field_validator("recent_entities", "participants")
    @classmethod
    def bound_memory(cls, value, info):
        return deque(value, maxlen=8 if info.field_name == "recent_entities" else 4)

    @field_validator("pronouns")
    @classmethod
    def require_pronoun_slots(cls, value):
        if set(value) != {"she", "he"}:
            raise ValueError("Both pronoun slots are required")
        return value


class PerformanceState(StateModel):
    voices: dict[str, str] = Field(default_factory=dict)
    voice_status: dict[str, Literal["provisional", "pending_recast", "locked"]] = Field(default_factory=dict)
    recast_used: dict[str, bool] = Field(default_factory=dict)
    female_index: int = Field(default=0, ge=0)
    male_index: int = Field(default=0, ge=0)
    unknown_index: int = Field(default=0, ge=0)


class LiteraryState(StateModel):
    # Immutable external evidence, deliberately outside runtime serialization.
    _book_prior: BookPreflight | None = PrivateAttr(default=None)
    schema_version: Literal[1] = 1
    # An opaque reading scope, not a book, protagonist, or detected chapter.
    scope_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, pattern=SCOPE_PATTERN)
    position: int = Field(default=0, ge=0)
    scene: SceneState = Field(default_factory=SceneState)
    performance: PerformanceState = Field(default_factory=PerformanceState)
    context: deque[ContextEntry] = Field(default_factory=lambda: deque(maxlen=CONTEXT_LENGTH), max_length=CONTEXT_LENGTH)

    @property
    def book_prior(self):
        return self._book_prior

    @classmethod
    def for_book(cls, prior: BookPreflight):
        """A deliberate book attachment starts a fresh literary/performance scope."""
        state = cls()
        state._book_prior = BookPreflight.from_json(prior.to_json())
        return state

    def pov_identity(self):
        if self.scene.pov_entity_id is None:
            self.scene.pov_entity_id = f"pov:{self.scope_id}"
        return self.scene.pov_entity_id

    @field_validator("context")
    @classmethod
    def bound_context(cls, value):
        return deque(value, maxlen=CONTEXT_LENGTH)

    @model_validator(mode="after")
    def check_scope(self):
        if self.scene.pov_entity_id not in (None, f"pov:{self.scope_id}"):
            raise ValueError("POV identity must belong to the current scope")
        previous = 0
        for entry in self.context:
            if entry.scope_id != self.scope_id or not previous < entry.position <= self.position:
                raise ValueError("Context must have ordered positions in the current scope")
            previous = entry.position
        decisions = [self.scene.active_decision]
        for entry in self.context:
            decisions.extend((entry.decision, entry.refinement))
        evidence = [e for d in decisions if d for e in d.evidence]
        evidence.extend(e for entry in self.context for e in entry.following_evidence)
        if any(e.scope_id != self.scope_id or e.position > self.position for e in evidence):
            raise ValueError("Evidence must refer to an observed position in the current scope")
        identities = [d.speaker for d in decisions if d]
        identities.extend(self.scene.recent_entities)
        identities.extend(self.scene.gender)
        identities.extend(self.scene.participants)
        identities.extend(self.performance.voices)
        identities.extend(self.performance.voice_status)
        identities.extend(self.performance.recast_used)
        identities.extend(self.scene.pronouns.values())
        identities.extend((self.scene.last_speaker, self.scene.previous_speaker, self.scene.pov_entity_id))
        for entry in self.context:
            identities.extend((entry.narrative_actor, entry.classification.speaker_name,
                               entry.performance_decision.speaker))
        identities.extend(e.candidate for e in evidence)
        for identity in identities:
            if identity is None:
                continue
            if identity.startswith("pov:"):
                if identity != self.scene.pov_entity_id:
                    raise ValueError("POV references must identify the current scope's POV entity")
            elif not re.fullmatch(r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+", identity):
                raise ValueError("Identity must be a named entity or a valid scoped POV, never a reserved role")
        return self

    def to_json(self):
        return self.model_dump_json()

    @classmethod
    def from_json(cls, value: str):
        envelope = json.loads(value)
        if not isinstance(envelope, dict) or set(envelope) != set(cls.model_fields):
            raise ValueError("A complete versioned state envelope is required")
        if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
            raise ValueError("Unsupported literary state schema version")
        return cls.model_validate_json(value)
