from collections import defaultdict
from contextlib import asynccontextmanager
from io import BytesIO
import json
import re
import wave

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from kokoro import KPipeline
from pydantic import BaseModel, Field

if __package__:
    from .book_preflight import BookPreflight
    from .literary_state import Classification, ContextEntry, Evidence, LiteraryState, SpeakerDecision, PerformanceDecision
else:
    from book_preflight import BookPreflight
    from literary_state import Classification, ContextEntry, Evidence, LiteraryState, SpeakerDecision, PerformanceDecision


@asynccontextmanager
async def lifespan(application):
    print(f"The Margin Proscenium v{application.version}", flush=True)
    yield


app = FastAPI(title="Local Kokoro Narration Server", version="0.16.0-dev", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# BEGIN TEMPORARY ingress diagnostic: remove this block after the live experiment.
from itertools import count

_ingress_numbers = count(1)  # Diagnostic only; resets when this process restarts.
_grouping_fields = (
    "request_id", "utterance_id", "session_id", "document_id", "book_id",
    "chapter_id", "paragraph_id", "span_id", "start_offset", "end_offset",
    "sequence", "sequence_number",
)


@app.middleware("http")
async def temporary_speech_ingress_diagnostic(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/v1/audio/speech":
        number = next(_ingress_numbers)
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            body = None
        # Read before model validation (which ignores extra fields). Never log
        # headers, query strings, arbitrary fields, or nested metadata objects.
        data = body if isinstance(body, dict) else {}
        text = data.get("input")
        escaped = (json.dumps(text, ensure_ascii=True).replace(" ", r"\u0020")
                   if isinstance(text, str) else "<missing or non-string input>")
        grouping = {key: data[key] for key in _grouping_fields
                    if key in data and isinstance(data[key], (str, int, float, bool, type(None)))}
        print(
            f"=== SPEECH INGRESS REQUEST {number} BEGIN ===\n"
            f"client={request.client.host if request.client else 'unknown'}\n"
            f"input_escaped={escaped}\n"
            f"grouping={json.dumps(grouping, ensure_ascii=True)}\n"
            f"=== SPEECH INGRESS REQUEST {number} END ===",
            flush=True,
        )
    return await call_next(request)

# END TEMPORARY ingress diagnostic.

PIPELINES = {
    "a": KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M"),
    "b": KPipeline(lang_code="b", repo_id="hexgrad/Kokoro-82M"),
}
DEFAULT_VOICE = "bf_isabella"
SAMPLE_RATE = 24000

FEMALE_VOICE_POOL = ["af_heart", "af_bella", "bf_isabella"]
MALE_VOICE_POOL = ["am_michael", "bm_george", "bm_daniel", "am_adam", "am_onyx"]
UNKNOWN_VOICE_POOL = ["bm_fable", "af_bella", "bm_george", "af_heart"]

SILENCE_THRESHOLD = 0.008
EDGE_SAFETY_MS = 18
LEADING_PAD_MS = 15
BASE_TRAILING_PAUSE_MS = {
    "continuation": 35, "comma": 90, "semicolon": 140, "period": 260,
    "question": 290, "exclamation": 290, "ellipsis": 420, "other": 180,
}

# Single-reader development selection; shared immutable evidence, not shared
# mutable literary/performance state. Explicit book changes clear every scope.
ACTIVE_BOOK_PRIOR: BookPreflight | None = None


def new_literary_state():
    return LiteraryState.for_book(ACTIVE_BOOK_PRIOR) if ACTIVE_BOOK_PRIOR is not None else LiteraryState()


LITERARY_STATES = defaultdict(new_literary_state)

ATTRIBUTION_VERBS = (
    "say", "says", "said", "ask", "asks", "asked", "answer", "answers", "answered",
    "reply", "replies", "replied", "add", "adds", "added", "continue", "continues",
    "continued", "call", "calls", "called", "shout", "shouts", "shouted", "whisper",
    "whispers", "whispered", "murmur", "murmurs", "murmured", "tell", "tells", "told",
    "cry", "cries", "cried", "remark", "remarks", "remarked", "observe", "observes",
    "observed", "insist", "insists", "insisted", "demand", "demands", "demanded",
    "wonder", "wonders", "wondered", "think", "thinks", "thought", "exclaim", "exclaims",
    "exclaimed", "respond", "responds", "responded", "retort", "retorts", "retorted",
    "announce", "announces", "announced", "admit", "admits", "admitted", "agree", "agrees",
    "agreed", "protest", "protests", "protested", "promise", "promises", "promised",
)
ATTRIBUTION_VERB_PATTERN = r"(?:" + "|".join(map(re.escape, ATTRIBUTION_VERBS)) + r")"
ATTRIBUTION_PATTERN = re.compile(rf"\b{ATTRIBUTION_VERB_PATTERN}\b", re.I)

NAME_STOPWORDS = {
    "A", "An", "And", "Are", "As", "Because", "But", "Come", "Did", "Do", "Does",
    "Don’t", "Don't", "For", "From", "Good", "He", "Her", "His", "How", "I", "If",
    "In", "Is", "It", "My", "No", "Not", "Now", "Oh", "On", "Our", "Please", "She",
    "So", "That", "The", "Their", "They", "This", "We", "What", "When", "Where", "Which",
    "Who", "Why", "With", "Yes", "You", "Your", "You’re", "You're", "Test",
}
# Sentence-initial grammatical words are not entities. This is deliberately
# category-oriented (determiners, connectives, locatives, indefinite pronouns,
# and adverbial/prepositional starters), rather than a blacklist of one story's
# false positives.
NON_ENTITY_STARTERS = {
    "After", "Although", "Another", "Any", "Anyone", "Anything", "At", "Before",
    "Behind", "Below", "Beneath", "Beside", "Between", "Beyond", "Closer", "Despite",
    "Either", "Enough", "Every", "Everyone", "Everything", "Farther", "Finally",
    "Inside", "Later", "Meanwhile", "Neither", "Nobody", "None", "Nothing", "Once",
    "Or", "Outside", "Over", "Perhaps", "Someone", "Something", "Soon", "Still",
    "Then", "There", "Though", "Through", "Toward", "Under", "Until", "Upon", "While",
}
PRONOUNS = {"i", "he", "she", "they", "we", "you", "him", "her", "them"}
NAME_BEFORE_ATTRIBUTION_PATTERN = re.compile(
    rf"\b([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+)\s+{ATTRIBUTION_VERB_PATTERN}\b"
)
ATTRIBUTION_BEFORE_NAME_PATTERN = re.compile(
    rf"\b{ATTRIBUTION_VERB_PATTERN}\s+([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+)\b", re.I
)
LEADING_REFERENCE_ATTRIBUTION_PATTERN = re.compile(
    rf"^\s*(I|he|she|they|we|you)\s+{ATTRIBUTION_VERB_PATTERN}\b", re.I
)
POST_DIALOGUE_REFERENCE_PATTERN = re.compile(
    rf"[”\"']\s*,?\s*(I|he|she|they|we|you)\s+{ATTRIBUTION_VERB_PATTERN}\b", re.I
)
SELF_ATTRIBUTION_PATTERN = re.compile(
    r"\b(I\s+(?:ask|asked|say|said|tell|told)\s+myself|I\s+(?:think|thought)|I\s+(?:wonder|wondered))\b",
    re.I,
)
LEADING_NAME_PATTERN = re.compile(r"^\s*([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+)\b")
LEADING_PRONOUN_PATTERN = re.compile(r"^\s*(I|She|He|They|We)\b", re.I)


def pipeline_for_voice(voice: str) -> KPipeline:
    return PIPELINES["a"] if voice.lower().startswith(("af_", "am_")) else PIPELINES["b"]


def valid_name_candidate(candidate):
    return bool(
        candidate
        and candidate[0].isupper()
        and candidate not in NAME_STOPWORDS
        and candidate not in NON_ENTITY_STARTERS
        and candidate.lower() not in PRONOUNS
    )


def attribution_surface(text: str, quote_was_open: bool = False) -> str:
    """Return only text outside quoted speech, preserving attached dialogue tags."""
    result = []
    curly_open = quote_was_open
    straight_open = quote_was_open
    for char in text:
        if char == "“":
            curly_open = True
            result.append(" ")
        elif char == "”":
            curly_open = False
            result.append(" ")
        elif char == '"':
            straight_open = not straight_open
            result.append(" ")
        elif not curly_open and not straight_open:
            result.append(char)
    return "".join(result)


def has_structural_attribution(text: str, quote_was_open: bool = False) -> bool:
    return bool(ATTRIBUTION_PATTERN.search(attribution_surface(text, quote_was_open)))


def segment_text(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    segments, buffer = [], []
    i = 0
    while i < len(text):
        char = text[i]
        buffer.append(char)
        if char == "\n":
            candidate = "".join(buffer).strip()
            if candidate:
                segments.append(candidate)
            buffer = []
            i += 1
            continue
        if char in ".!?":
            lookahead = i + 1
            while lookahead < len(text) and text[lookahead] in '”"’\')]}':
                buffer.append(text[lookahead])
                lookahead += 1
            whitespace_start = lookahead
            while lookahead < len(text) and text[lookahead].isspace() and text[lookahead] != "\n":
                lookahead += 1
            if lookahead >= len(text):
                i = lookahead
                break
            plausible = text[lookahead].isupper() or text[lookahead].isdigit() or text[lookahead] in '“"‘\''
            if plausible and lookahead > whitespace_start:
                candidate = "".join(buffer).strip()
                if candidate:
                    segments.append(candidate)
                buffer = []
                i = lookahead
                continue
        i += 1
    remainder = "".join(buffer).strip()
    if remainder:
        segments.append(remainder)
    return [s for s in segments if s.strip()]


def update_quote_state(client_id: str, text: str) -> dict:
    state = LITERARY_STATES[client_id].scene
    was_open = state.quote_open
    opens, closes, straight = text.count("“"), text.count("”"), text.count('"')
    stripped = text.strip()
    if opens or closes:
        if was_open:
            if opens > 0 and closes < opens:
                dialogue, state.quote_open, transition = True, True, "dialogue_reopen"
            elif closes > opens or (stripped.endswith("”") and closes):
                dialogue, state.quote_open, transition = True, False, "dialogue_close"
            else:
                dialogue, state.quote_open, transition = True, True, "dialogue_continuation"
        elif opens:
            dialogue = True
            state.quote_open = closes < opens
            if state.quote_open:
                transition = "dialogue_open"
            else:
                transition = "dialogue_with_attribution" if has_structural_attribution(text, was_open) else "dialogue_complete"
        else:
            dialogue, state.quote_open = True, False
            transition = "orphan_dialogue_with_attribution" if has_structural_attribution(text, was_open) else "orphan_dialogue_close"
    elif straight:
        dialogue = True
        if was_open:
            state.quote_open = straight % 2 == 0
            transition = "dialogue_continuation" if state.quote_open else "dialogue_close"
        else:
            state.quote_open = straight % 2 == 1
            transition = "dialogue_open" if state.quote_open else (
                "dialogue_with_attribution" if has_structural_attribution(text, was_open) else "dialogue_complete"
            )
    elif was_open:
        dialogue, transition = True, "dialogue_continuation"
    else:
        dialogue, state.quote_open, transition = False, False, "outside_dialogue"
    return {
        "was_open": was_open, "is_dialogue": dialogue, "quote_transition": transition,
        "quote_open_after": state.quote_open, "curly_open_count": opens,
        "curly_close_count": closes, "straight_count": straight,
    }


def extract_speaker_hint(text: str, quote_was_open: bool = False) -> dict:
    text = attribution_surface(text, quote_was_open)
    for pattern in (NAME_BEFORE_ATTRIBUTION_PATTERN, ATTRIBUTION_BEFORE_NAME_PATTERN):
        match = pattern.search(text)
        if match and valid_name_candidate(match.group(1)):
            return {"speaker_name": match.group(1), "speaker_reference": None}
    for pattern in (LEADING_REFERENCE_ATTRIBUTION_PATTERN, POST_DIALOGUE_REFERENCE_PATTERN):
        match = pattern.search(text)
        if match:
            return {"speaker_name": None, "speaker_reference": match.group(1)}
    if SELF_ATTRIBUTION_PATTERN.search(text):
        return {"speaker_name": None, "speaker_reference": "I"}
    return {"speaker_name": None, "speaker_reference": None}


def classify_utterance(client_id: str, text: str) -> dict:
    stripped = text.strip()
    if not stripped:
        return {"type": "empty", "speaker_name": None, "speaker_reference": None, "confidence": "high"}
    quote = update_quote_state(client_id, stripped)
    speaker = extract_speaker_hint(stripped, quote["was_open"])
    transition = quote["quote_transition"]
    types = {
        "dialogue_with_attribution": ("dialogue_with_attribution", "high"),
        "dialogue_open": ("dialogue_open", "high"), "dialogue_reopen": ("dialogue_reopen", "medium"),
        "dialogue_continuation": ("dialogue_continuation", "high"),
        "dialogue_close": ("dialogue_close", "high"), "dialogue_complete": ("dialogue", "high"),
        "orphan_dialogue_close": ("dialogue_orphan_close", "medium"),
        "orphan_dialogue_with_attribution": ("dialogue_orphan_with_attribution", "medium"),
    }
    if transition in types:
        kind, confidence = types[transition]
    elif has_structural_attribution(stripped, quote["was_open"]):
        kind, confidence = "possible_attribution", "medium"
    else:
        kind, confidence = "narration", "medium"
    return {"type": kind, "confidence": confidence, "has_attribution": has_structural_attribution(stripped, quote["was_open"]), **speaker, **quote}


def remember_entity(client_id: str, name: str):
    if not valid_name_candidate(name):
        return
    entities = LITERARY_STATES[client_id].scene.recent_entities
    try:
        entities.remove(name)
    except ValueError:
        pass
    entities.append(name)


def most_recent_entity(client_id: str, gender=None):
    state = LITERARY_STATES[client_id].scene
    for name in reversed(state.recent_entities):
        if gender is None or state.gender.get(name) == gender:
            return name
    if gender:
        for name in reversed(state.recent_entities):
            if state.gender.get(name) is None:
                return name
    return None


def next_available_voice(pool, narrator_voice, index):
    if not pool:
        return narrator_voice, index
    for offset in range(len(pool)):
        pos = (index + offset) % len(pool)
        if pool[pos] != narrator_voice:
            return pool[pos], pos + 1
    return pool[index % len(pool)], index + 1


def choose_pool_voice(state, gender, narrator_voice):
    if gender == "female":
        pool, key = FEMALE_VOICE_POOL, "female_index"
    elif gender == "male":
        pool, key = MALE_VOICE_POOL, "male_index"
    else:
        pool, key = UNKNOWN_VOICE_POOL, "unknown_index"
    voice, next_index = next_available_voice(pool, narrator_voice, getattr(state, key))
    setattr(state, key, next_index)
    return voice


def refine_gender(client_id: str, speaker: str, gender: str):
    """Apply a pronoun binding and permit exactly one provisional recast."""
    if not speaker:
        return
    state = LITERARY_STATES[client_id].scene
    performance = LITERARY_STATES[client_id].performance
    if state.gender.get(speaker) is None:
        state.gender[speaker] = gender
        if speaker in performance.voices and performance.voice_status.get(speaker) == "provisional" and not performance.recast_used.get(speaker, False):
            performance.voices.pop(speaker, None)
            performance.recast_used[speaker] = True
            performance.voice_status[speaker] = "pending_recast"
    # Later contradictory guesses cannot oscillate gender or voice.


def bind_pronoun(client_id: str, reference: str):
    state = LITERARY_STATES[client_id].scene
    ref = reference.lower()
    if ref == "i":
        return LITERARY_STATES[client_id].pov_identity(), None
    if ref in ("she", "her", "he", "him"):
        slot, gender = ("she", "female") if ref in ("she", "her") else ("he", "male")
        cached = state.pronouns[slot]
        if cached and state.gender.get(cached) not in (None, gender):
            cached = None
        speaker = cached or most_recent_entity(client_id, gender)
        state.pronouns[slot] = speaker
        refine_gender(client_id, speaker, gender)
        return speaker, gender
    return None, None


def assign_cast_voice(client_id: str, speaker: str | None, gender, narrator_voice: str):
    # Fallback is performance only: never allocate a cast identity or voice for it.
    if speaker is None:
        return narrator_voice
    literary = LITERARY_STATES[client_id]
    state = literary.performance
    if speaker in state.voices:
        return state.voices[speaker]
    known_gender = literary.scene.gender.get(speaker) or gender
    voice = choose_pool_voice(state, known_gender, narrator_voice)
    state.voices[speaker] = voice
    if known_gender:
        state.voice_status[speaker] = "locked"
    else:
        state.voice_status[speaker] = "provisional"
        state.recast_used.setdefault(speaker, False)
    return voice


def observe_narrative_actor(client_id: str, text: str):
    """Return a named/pronominal actor for a narration/action sentence."""
    state = LITERARY_STATES[client_id].scene
    match = LEADING_NAME_PATTERN.search(text)
    if match and valid_name_candidate(match.group(1)):
        actor = match.group(1)
        literary = LITERARY_STATES[client_id]
        if literary.book_prior and not literary.book_prior.find_character(actor):
            # Absence rejects only weak action evidence. Current attribution
            # remains authoritative, including personified weather.
            explicit = extract_speaker_hint(text).get("speaker_name")
            observed = any(
                e.kind == "explicit-name" and e.strength == "definitive" and e.candidate == actor
                for entry in literary.context
                for decision in (entry.decision, entry.refinement) if decision
                for e in decision.evidence
            ) or actor in state.participants
            if explicit != actor and not observed:
                return None
        remember_entity(client_id, actor)
        # Possessive pronouns in a named actor sentence are reliable trait clues.
        if re.search(r"\bher\b", text, re.I):
            refine_gender(client_id, actor, "female")
            state.pronouns["she"] = actor
        elif re.search(r"\bhis\b", text, re.I):
            refine_gender(client_id, actor, "male")
            state.pronouns["he"] = actor
        return actor
    match = LEADING_PRONOUN_PATTERN.search(text)
    if match and match.group(1).lower() in ("she", "he"):
        return bind_pronoun(client_id, match.group(1))[0]
    return None


def record_dialogue_speaker(state, decision):
    """Only established attribution may add confirmed conversation participants."""
    if decision.status != "resolved":
        # An unknown/tentative intervening turn breaks known turn ordering.
        state.previous_speaker = state.last_speaker
        state.last_speaker = None
        return
    speaker = decision.speaker
    if speaker != state.last_speaker:
        state.previous_speaker = state.last_speaker
        state.last_speaker = speaker
    try:
        state.participants.remove(speaker)
    except ValueError:
        pass
    state.participants.append(speaker)


def attribution_decision(client_id, text, classification):
    literary = LITERARY_STATES[client_id]
    name, reference = classification.get("speaker_name"), classification.get("speaker_reference")
    if name:
        remember_entity(client_id, name)
        speaker, status, kind = name, "resolved", "explicit-name"
    elif reference:
        speaker, _ = bind_pronoun(client_id, reference)
        # Third-person reference binding still uses recency heuristics.
        status = "resolved" if reference.lower() == "i" else ("tentative" if speaker else "unresolved")
        kind = "explicit-reference"
    else:
        return None
    evidence = Evidence(kind=kind, scope_id=literary.scope_id, position=literary.position,
                        text=text, candidate=speaker,
                        strength="definitive" if status == "resolved" else "suggestive")
    return SpeakerDecision(status=status, speaker=speaker, reason=kind,
                           confidence="high" if status == "resolved" else "low", evidence=(evidence,))


def infer_speaker(client_id: str, text: str, classification: dict):
    literary = LITERARY_STATES[client_id]
    state = literary.scene
    if not classification["is_dialogue"]:
        return SpeakerDecision(status="narration", reason="narration", confidence="high")

    decision = attribution_decision(client_id, text, classification)
    if decision is None and classification.get("was_open") and state.active_decision is not None:
        active = state.active_decision
        evidence = Evidence(kind="quote-span-continuation", scope_id=literary.scope_id,
                            position=literary.position, text=text, candidate=active.speaker,
                            strength="definitive" if active.status == "resolved" else "suggestive")
        decision = SpeakerDecision(status=active.status, speaker=active.speaker,
                                   reason="quote-span-continuation", confidence=active.confidence,
                                   evidence=(*active.evidence[:3], evidence))
    if decision is None:
        evidence = []
        prior = literary.context[-1] if literary.context else None
        if (prior and prior.position == literary.position - 1 and prior.narrative_actor
                and not prior.classification.has_attribution):
            evidence.append(Evidence(kind="preceding-action", scope_id=literary.scope_id,
                                     position=prior.position, text=prior.text,
                                     candidate=prior.narrative_actor, strength="suggestive"))
        # Preserve alternation as inspectable evidence, never as a forced identity.
        if len(state.participants) == 2 and state.last_speaker in state.participants:
            candidate = next(p for p in state.participants if p != state.last_speaker)
            evidence.append(Evidence(kind="two-person-alternation", scope_id=literary.scope_id,
                                     position=literary.position, text=text,
                                     candidate=candidate, strength="suggestive"))
        decision = SpeakerDecision(status="unresolved", reason="conservative-dialogue-fallback",
                                   confidence="low", evidence=tuple(evidence))
    state.active_decision = decision if classification["quote_open_after"] else None
    record_dialogue_speaker(state, decision)
    return decision


def is_explicit_detached_speech_tag(entry: ContextEntry):
    """Conservative confirmation gate; leave the general attribution parser intact.

    Only bare subject/verb speech tags qualify. Complements, negation, thought
    verbs, and other prose remain observations even when the parser finds a hint.
    """
    subject = entry.classification.speaker_name or entry.classification.speaker_reference
    if not subject:
        return False
    subject = re.escape(subject)
    verb = r"(?:say|says|said|ask|asks|asked|reply|replies|replied|answer|answers|answered|whisper|whispers|whispered|shout|shouts|shouted|murmur|murmurs|murmured|exclaim|exclaims|exclaimed|respond|responds|responded|retort|retorts|retorted)"
    return bool(re.fullmatch(
        rf"\s*(?:{subject}\s+{verb}|{verb}\s+{subject})\s*[.!?]?\s*",
        entry.text, re.I,
    ))


def update_scene_after_narration(client_id: str, entry: ContextEntry):
    """Keep later evidence separate from the immutable decision used for rendering."""
    literary = LITERARY_STATES[client_id]
    classification = entry.classification
    if classification.is_dialogue:
        return
    prior = literary.context[-2] if len(literary.context) >= 2 else None
    if not (prior and prior.position == entry.position - 1 and prior.classification.is_dialogue):
        return
    if classification.type == "possible_attribution" and is_explicit_detached_speech_tag(entry):
        if prior.decision.status in ("unresolved", "tentative"):
            refinement = attribution_decision(client_id, entry.text, classification.model_dump())
            prior.refinement = refinement
            record_dialogue_speaker(literary.scene, refinement)
        return
    if entry.narrative_actor:
        prior.following_evidence = (Evidence(
            kind="following-action", scope_id=literary.scope_id, position=entry.position,
            text=entry.text, candidate=entry.narrative_actor, strength="suggestive",
        ),)


def decide_performance(literary: LiteraryState, decision: SpeakerDecision):
    """Read literary evidence without writing back a rendering guess.

    Quote continuations retain their original literary evidence. Cast membership
    alone is deliberately not proof that a candidate is a literary entity.
    """
    if decision.status in ("resolved", "tentative"):
        return PerformanceDecision(speaker=decision.speaker, reason=decision.reason,
                                   confidence=decision.confidence)
    if decision.status == "unresolved":
        cues = [e for e in decision.evidence
                if e.kind in ("preceding-action", "two-person-alternation") and e.candidate]
        candidates = {e.candidate for e in cues}
        established = set(literary.scene.recent_entities) | set(literary.scene.participants)
        if literary.scene.pov_entity_id:
            established.add(literary.scene.pov_entity_id)
        if len(candidates) == 1 and candidates <= established:
            return PerformanceDecision(speaker=cues[0].candidate,
                                       reason="tentative-" + cues[0].kind, confidence="low")
    return PerformanceDecision(reason="narration" if decision.status == "narration"
                               else "insufficient-evidence-narrator-fallback",
                               confidence="high" if decision.status == "narration" else "low")


def prepare_segment(client_id: str, text: str, narrator_voice: str):
    """Shared routing path for HTTP and model-free regression tests."""
    literary = LITERARY_STATES[client_id]
    literary.position += 1
    classification = classify_utterance(client_id, text)
    actor = None if classification["is_dialogue"] else observe_narrative_actor(client_id, text)
    decision = infer_speaker(client_id, text, classification)
    performance_decision = decide_performance(literary, decision)
    gender = literary.scene.gender.get(performance_decision.speaker)
    voice = narrator_voice if not classification["is_dialogue"] else assign_cast_voice(
        client_id, performance_decision.speaker, gender, narrator_voice
    )
    return ContextEntry(
        scope_id=literary.scope_id, position=literary.position, text=text,
        classification=Classification(**classification), narrative_actor=actor,
        decision=decision, performance_decision=performance_decision,
        selected_voice=voice, cast_gender=gender,
        voice_status=literary.performance.voice_status.get(performance_decision.speaker, "narrator"),
    )


def record_segment(client_id: str, entry: ContextEntry):
    LITERARY_STATES[client_id].context.append(entry)
    update_scene_after_narration(client_id, entry)


def ending_type(text: str):
    stripped = re.sub(r'[”"’\')\]}\s]+$', "", text.strip())
    if stripped.endswith("...") or stripped.endswith("…"):
        return "ellipsis"
    if stripped.endswith("?"):
        return "question"
    if stripped.endswith("!"):
        return "exclamation"
    if stripped.endswith("."):
        return "period"
    if stripped.endswith(";") or stripped.endswith(":"):
        return "semicolon"
    if stripped.endswith(","):
        return "comma"
    return "continuation" if stripped else "other"


def trim_and_pad(audio: np.ndarray, text: str, classification: dict):
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if not len(audio):
        return audio, {}
    active = np.flatnonzero(np.abs(audio) > SILENCE_THRESHOLD)
    safety = int(SAMPLE_RATE * EDGE_SAFETY_MS / 1000)
    if len(active):
        start = max(0, int(active[0]) - safety)
        end = min(len(audio), int(active[-1]) + safety + 1)
    else:
        start, end = 0, len(audio)
    core = audio[start:end]
    ending = ending_type(text)
    trailing = BASE_TRAILING_PAUSE_MS[ending]
    reason = "baseline"
    if classification.get("is_dialogue"):
        if ending == "question":
            trailing, reason = 310, "dialogue-turn-question"
        elif ending in ("period", "exclamation"):
            trailing, reason = 285, "dialogue-turn-end"
    elif classification.get("type") == "possible_attribution":
        trailing, reason = 205, "dialogue-attribution"
    leading_samples = int(SAMPLE_RATE * LEADING_PAD_MS / 1000)
    trailing_samples = int(SAMPLE_RATE * trailing / 1000)
    result = np.concatenate((np.zeros(leading_samples, np.float32), core, np.zeros(trailing_samples, np.float32)))
    return result, {
        "ending": ending, "reason": reason, "removed_leading_ms": start * 1000 / SAMPLE_RATE,
        "removed_trailing_ms": (len(audio) - end) * 1000 / SAMPLE_RATE,
        "added_leading_ms": LEADING_PAD_MS, "added_trailing_ms": trailing,
    }


def synthesize(text: str, voice: str, speed: float):
    chunks = []
    for result in pipeline_for_voice(voice)(text, voice=voice, speed=speed, split_pattern=r"\n+"):
        audio = result.audio if hasattr(result, "audio") else result[2]
        chunks.append(np.asarray(audio, dtype=np.float32).reshape(-1))
    if not chunks:
        raise RuntimeError("Kokoro returned no audio")
    return np.concatenate(chunks)


def wav_bytes(audio: np.ndarray):
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
    return output.getvalue()


class SpeechRequest(BaseModel):
    model: str = "kokoro"
    input: str = Field(min_length=1)
    voice: str = DEFAULT_VOICE
    response_format: str = "wav"
    speed: float = Field(default=1.0, gt=0.25, le=4.0)


@app.get("/health")
def health():
    return {"status": "ok", "version": app.version, "sample_rate": SAMPLE_RATE}


@app.get("/cast")
def cast(request: Request):
    client_id = request.client.host if request.client else "unknown"
    literary = LITERARY_STATES[client_id]
    state = literary.scene
    return {
        "client": client_id, **literary.performance.model_dump(mode="json"),
        "gender": dict(state.gender), "recent_entities": list(state.recent_entities),
        "pronouns": dict(state.pronouns), "participants": list(state.participants),
        "last_speaker": state.last_speaker, "previous_speaker": state.previous_speaker,
        # Retained for diagnostic compatibility; action cues no longer select speakers.
        "likely_next_speaker": None,
        "schema_version": literary.schema_version, "scope_id": literary.scope_id,
        "pov_entity_id": state.pov_entity_id,
    }


@app.get("/context")
def context(request: Request):
    client_id = request.client.host if request.client else "unknown"
    literary = LITERARY_STATES[client_id]
    return {"client": client_id, "quote_open": literary.scene.quote_open,
            "schema_version": literary.schema_version, "scope_id": literary.scope_id,
            "utterances": [entry.public_record() for entry in literary.context]}


@app.post("/context/reset")
def reset_context(request: Request):
    client_id = request.client.host if request.client else "unknown"
    LITERARY_STATES.pop(client_id, None)
    return {"status": "reset", "client": client_id}


@app.post("/book/prior")
def attach_book_prior(payload: BookPreflight, request: Request):
    """Select the book for all clients in this single-reader server process."""
    global ACTIVE_BOOK_PRIOR
    # Validate before replacing the selection or discarding any client state.
    prior = BookPreflight.from_json(payload.to_json())
    client_id = request.client.host if request.client else "unknown"
    ACTIVE_BOOK_PRIOR = prior
    LITERARY_STATES.clear()
    literary = LITERARY_STATES[client_id]
    return {"client": client_id, "attachment_scope": "server", "scope_id": literary.scope_id,
            "document": prior.document.model_dump(mode="json"),
            "characters": len(prior.characters)}


@app.get("/book/prior")
def book_prior(request: Request):
    client_id = request.client.host if request.client else "unknown"
    return {"client": client_id, "attachment_scope": "server",
            "prior": ACTIVE_BOOK_PRIOR.model_dump(mode="json") if ACTIVE_BOOK_PRIOR else None}


@app.delete("/book/prior")
def detach_book_prior(request: Request):
    global ACTIVE_BOOK_PRIOR
    ACTIVE_BOOK_PRIOR = None
    LITERARY_STATES.clear()
    client_id = request.client.host if request.client else "unknown"
    return {"status": "reset", "client": client_id, "attachment_scope": "server"}


@app.post("/v1/audio/speech")
def audio_speech(payload: SpeechRequest, request: Request):
    if payload.response_format.lower() not in ("wav", "wave"):
        raise HTTPException(status_code=400, detail="Only WAV output is supported")
    client_id = request.client.host if request.client else "unknown"
    segments = segment_text(payload.input)
    if not segments:
        raise HTTPException(status_code=400, detail="Input contains no speakable text")
    rendered = []
    try:
        for index, text in enumerate(segments, 1):
            entry = prepare_segment(client_id, text, payload.voice)
            audio = synthesize(text, entry.selected_voice, payload.speed)
            audio, pacing = trim_and_pad(audio, text, entry.classification.model_dump())
            rendered.append(audio)
            record_segment(client_id, entry)
            print(json.dumps({"segment": f"{index}/{len(segments)}", "client": client_id,
                              **entry.public_record(), "pacing": pacing}, ensure_ascii=False))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Speech synthesis failed: {exc}") from exc
    combined = np.concatenate(rendered)
    return Response(content=wav_bytes(combined), media_type="audio/wav", headers={"Content-Disposition": "inline; filename=speech.wav"})
