from collections import defaultdict, deque
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


app = FastAPI(title="Local Kokoro Narration Server", version="0.15.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

CONTEXT_LENGTH = 16
CONTEXTS = defaultdict(lambda: deque(maxlen=CONTEXT_LENGTH))
DIALOGUE_STATE = defaultdict(lambda: {"quote_open": False})


def new_cast_state():
    return {
        "voices": {},
        "gender": {},
        # An unknown-gender assignment is provisional. A reliable she/he binding
        # may replace it once; all gendered assignments are immediately locked.
        "voice_status": {},
        "recast_used": {},
        "recent_entities": deque(maxlen=8),
        "pronouns": {"she": None, "he": None},
        "active_dialogue_speaker": None,
        "last_speaker": None,
        "previous_speaker": None,
        "participants": deque(maxlen=4),
        "likely_next_speaker": None,
        "female_index": 0,
        "male_index": 0,
        "unknown_index": 0,
    }


CAST_STATE = defaultdict(new_cast_state)

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
    state = DIALOGUE_STATE[client_id]
    was_open = state["quote_open"]
    opens, closes, straight = text.count("“"), text.count("”"), text.count('"')
    stripped = text.strip()
    if opens or closes:
        if was_open:
            if opens > 0 and closes < opens:
                dialogue, state["quote_open"], transition = True, True, "dialogue_reopen"
            elif closes > opens or (stripped.endswith("”") and closes):
                dialogue, state["quote_open"], transition = True, False, "dialogue_close"
            else:
                dialogue, state["quote_open"], transition = True, True, "dialogue_continuation"
        elif opens:
            dialogue = True
            state["quote_open"] = closes < opens
            if state["quote_open"]:
                transition = "dialogue_open"
            else:
                transition = "dialogue_with_attribution" if has_structural_attribution(text, was_open) else "dialogue_complete"
        else:
            dialogue, state["quote_open"] = True, False
            transition = "orphan_dialogue_with_attribution" if has_structural_attribution(text, was_open) else "orphan_dialogue_close"
    elif straight:
        dialogue = True
        if was_open:
            state["quote_open"] = straight % 2 == 0
            transition = "dialogue_continuation" if state["quote_open"] else "dialogue_close"
        else:
            state["quote_open"] = straight % 2 == 1
            transition = "dialogue_open" if state["quote_open"] else (
                "dialogue_with_attribution" if has_structural_attribution(text, was_open) else "dialogue_complete"
            )
    elif was_open:
        dialogue, transition = True, "dialogue_continuation"
    else:
        dialogue, state["quote_open"], transition = False, False, "outside_dialogue"
    return {
        "was_open": was_open, "is_dialogue": dialogue, "quote_transition": transition,
        "quote_open_after": state["quote_open"], "curly_open_count": opens,
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
    entities = CAST_STATE[client_id]["recent_entities"]
    try:
        entities.remove(name)
    except ValueError:
        pass
    entities.append(name)


def most_recent_entity(client_id: str, gender=None):
    state = CAST_STATE[client_id]
    for name in reversed(state["recent_entities"]):
        if gender is None or state["gender"].get(name) == gender:
            return name
    if gender:
        for name in reversed(state["recent_entities"]):
            if state["gender"].get(name) is None:
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
    voice, state[key] = next_available_voice(pool, narrator_voice, state[key])
    return voice


def refine_gender(client_id: str, speaker: str, gender: str):
    """Apply a reliable pronoun binding and permit exactly one provisional recast."""
    if speaker.startswith("__"):
        CAST_STATE[client_id]["gender"][speaker] = gender
        return
    state = CAST_STATE[client_id]
    old_gender = state["gender"].get(speaker)
    if old_gender is None:
        state["gender"][speaker] = gender
        if speaker in state["voices"] and state["voice_status"].get(speaker) == "provisional" and not state["recast_used"].get(speaker, False):
            state["voices"].pop(speaker, None)
            state["recast_used"][speaker] = True
            state["voice_status"][speaker] = "pending_recast"
    # Once a reliable gender has been learned, conflicting later guesses cannot
    # oscillate either gender or voice.


def bind_pronoun(client_id: str, reference: str):
    state = CAST_STATE[client_id]
    ref = reference.lower()
    if ref == "i":
        return "__narrator__", None
    if ref in ("she", "her"):
        cached = state["pronouns"]["she"]
        if cached and state["gender"].get(cached) not in (None, "female"):
            cached = None
        speaker = cached or most_recent_entity(client_id, "female") or "__she__"
        state["pronouns"]["she"] = speaker
        refine_gender(client_id, speaker, "female")
        return speaker, "female"
    if ref in ("he", "him"):
        cached = state["pronouns"]["he"]
        if cached and state["gender"].get(cached) not in (None, "male"):
            cached = None
        speaker = cached or most_recent_entity(client_id, "male") or "__he__"
        state["pronouns"]["he"] = speaker
        refine_gender(client_id, speaker, "male")
        return speaker, "male"
    return None, None


def assign_cast_voice(client_id: str, speaker: str, gender, narrator_voice: str):
    if speaker == "__narrator__":
        return narrator_voice
    state = CAST_STATE[client_id]
    if speaker in state["voices"]:
        return state["voices"][speaker]
    known_gender = state["gender"].get(speaker) or gender
    voice = choose_pool_voice(state, known_gender, narrator_voice)
    state["voices"][speaker] = voice
    if known_gender:
        state["gender"][speaker] = known_gender
        state["voice_status"][speaker] = "locked"
    else:
        state["voice_status"][speaker] = "provisional"
        state["recast_used"].setdefault(speaker, False)
    return voice


def observe_narrative_actor(client_id: str, text: str):
    """Return a named/pronominal actor for a narration/action sentence."""
    state = CAST_STATE[client_id]
    match = LEADING_NAME_PATTERN.search(text)
    if match and valid_name_candidate(match.group(1)):
        actor = match.group(1)
        remember_entity(client_id, actor)
        # Possessive pronouns in a named actor sentence are reliable trait clues.
        if re.search(r"\bher\b", text, re.I):
            refine_gender(client_id, actor, "female")
            state["pronouns"]["she"] = actor
        elif re.search(r"\bhis\b", text, re.I):
            refine_gender(client_id, actor, "male")
            state["pronouns"]["he"] = actor
        return actor
    match = LEADING_PRONOUN_PATTERN.search(text)
    if match and match.group(1).lower() in ("she", "he"):
        return bind_pronoun(client_id, match.group(1))[0]
    return None


def add_participant(state, speaker):
    if not speaker or speaker.startswith("__") and speaker != "__narrator__":
        return
    try:
        state["participants"].remove(speaker)
    except ValueError:
        pass
    state["participants"].append(speaker)


def record_dialogue_speaker(state, speaker):
    if not speaker:
        return
    if speaker != state["last_speaker"]:
        state["previous_speaker"] = state["last_speaker"]
        state["last_speaker"] = speaker
    add_participant(state, speaker)


def previous_dialogue_entry(client_id: str):
    for item in reversed(CONTEXTS[client_id]):
        if item.get("is_dialogue"):
            return item
    return None


def infer_speaker(client_id: str, text: str, classification: dict):
    state = CAST_STATE[client_id]
    kind = classification["type"]
    is_dialogue = classification["is_dialogue"]
    name = classification.get("speaker_name")
    reference = classification.get("speaker_reference")

    if name:
        remember_entity(client_id, name)
        speaker, gender, reason, confidence = name, state["gender"].get(name), (
            "explicit-name" if is_dialogue else "narration-attribution"
        ), "high"
    elif reference:
        speaker, gender = bind_pronoun(client_id, reference)
        reason, confidence = (
            "explicit-reference" if is_dialogue else "narration-attribution"
        ), "high"
    elif is_dialogue and classification.get("was_open") and state["active_dialogue_speaker"]:
        speaker, gender, reason, confidence = state["active_dialogue_speaker"], None, "quote-span-continuation", "high"
    elif is_dialogue and state["likely_next_speaker"]:
        speaker = state["likely_next_speaker"]
        state["likely_next_speaker"] = None
        gender, reason, confidence = state["gender"].get(speaker), "narrative-action-cue", "medium"
    elif is_dialogue:
        participants = list(state["participants"])
        if len(participants) == 2 and state["last_speaker"] in participants:
            speaker = next(p for p in participants if p != state["last_speaker"])
            gender, reason, confidence = state["gender"].get(speaker), "two-person-alternation", "medium"
        elif len(participants) == 1 and participants[0] != state["last_speaker"]:
            speaker = participants[0]
            gender, reason, confidence = state["gender"].get(speaker), "known-participant", "low"
        else:
            speaker, gender, reason, confidence = "__narrator__", None, "conservative-dialogue-fallback", "low"
    else:
        speaker, gender, reason, confidence = "__narrator__", None, "narration", "high"

    if is_dialogue:
        # An explicit speaker overrides (and therefore invalidates) any pending
        # action prediction instead of leaving it to leak into the next turn.
        if name or reference:
            state["likely_next_speaker"] = None
        if classification["quote_open_after"]:
            state["active_dialogue_speaker"] = speaker
        else:
            state["active_dialogue_speaker"] = None
        record_dialogue_speaker(state, speaker)
    return speaker, gender, reason, confidence


def update_scene_after_narration(client_id: str, text: str, classification: dict, actor):
    state = CAST_STATE[client_id]
    if classification["is_dialogue"]:
        return None
    prior = previous_dialogue_entry(client_id)
    previous_speaker = prior.get("cast_speaker") if prior else state["last_speaker"]

    # Backward attribution learns who delivered the immediately preceding line.
    if classification["type"] == "possible_attribution" and (
        classification.get("speaker_reference") or classification.get("speaker_name")
    ):
        if classification.get("speaker_name"):
            attributed = classification["speaker_name"]
            remember_entity(client_id, attributed)
        else:
            attributed, _ = bind_pronoun(client_id, classification["speaker_reference"])
        if prior and attributed:
            prior["cast_speaker"] = attributed
            record_dialogue_speaker(state, attributed)
        # This explicit attribution supersedes any older next-speaker cue.
        state["likely_next_speaker"] = None
        return "narration-attribution"

    if not actor:
        return None
    if (
        prior
        and prior.get("routing_reason") == "conservative-dialogue-fallback"
        and previous_speaker in (None, "__narrator__")
        and actor != previous_speaker
    ):
        # A following named action can retrospectively identify an otherwise
        # unknown/fallback dialogue line.
        prior["cast_speaker"] = actor
        record_dialogue_speaker(state, actor)
        previous_speaker = actor

    if actor == previous_speaker:
        # The beat confirms the preceding line without consuming its separate,
        # forward-looking value for the immediately following dialogue.
        state["likely_next_speaker"] = actor
        return "action-confirms-previous"
    else:
        # A different actor remains a plausible cue for the following line.
        state["likely_next_speaker"] = actor
        return "narrative-action-cue"


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
    state = CAST_STATE[client_id]
    return {
        "client": client_id, "voices": dict(state["voices"]), "gender": dict(state["gender"]),
        "voice_status": dict(state["voice_status"]), "recast_used": dict(state["recast_used"]),
        "recent_entities": list(state["recent_entities"]), "pronouns": dict(state["pronouns"]),
        "participants": list(state["participants"]), "last_speaker": state["last_speaker"],
        "previous_speaker": state["previous_speaker"], "likely_next_speaker": state["likely_next_speaker"],
    }


@app.get("/context")
def context(request: Request):
    client_id = request.client.host if request.client else "unknown"
    return {"client": client_id, "quote_open": DIALOGUE_STATE[client_id]["quote_open"], "utterances": list(CONTEXTS[client_id])}


@app.post("/context/reset")
def reset_context(request: Request):
    client_id = request.client.host if request.client else "unknown"
    CONTEXTS.pop(client_id, None)
    DIALOGUE_STATE.pop(client_id, None)
    CAST_STATE.pop(client_id, None)
    return {"status": "reset", "client": client_id}


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
            classification = classify_utterance(client_id, text)
            actor = None if classification["is_dialogue"] else observe_narrative_actor(client_id, text)
            speaker, gender, reason, speaker_confidence = infer_speaker(client_id, text, classification)
            # Attribution prose can carry character semantics and update cast
            # memory, but it remains prose and must always use the narrator.
            voice = payload.voice if not classification["is_dialogue"] else assign_cast_voice(
                client_id, speaker, gender, payload.voice
            )
            audio = synthesize(text, voice, payload.speed)
            audio, pacing = trim_and_pad(audio, text, classification)
            rendered.append(audio)
            entry = {
                "text": text, **classification, "narrative_actor": actor, "cast_speaker": speaker,
                "cast_gender": CAST_STATE[client_id]["gender"].get(speaker), "selected_voice": voice,
                "routing_reason": reason, "speaker_confidence": speaker_confidence,
                "voice_status": CAST_STATE[client_id]["voice_status"].get(speaker, "narrator"),
            }
            CONTEXTS[client_id].append(entry)
            narrative_reason = update_scene_after_narration(client_id, text, classification, actor)
            if narrative_reason:
                entry["routing_reason"] = narrative_reason
            print(json.dumps({"segment": f"{index}/{len(segments)}", "client": client_id, **entry, "pacing": pacing}, ensure_ascii=False, default=str))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Speech synthesis failed: {exc}") from exc
    combined = np.concatenate(rendered)
    return Response(content=wav_bytes(combined), media_type="audio/wav", headers={"Content-Disposition": "inline; filename=speech.wav"})
