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


# ===========================================================================
# Application
# ===========================================================================

app = FastAPI(
    title="Local Kokoro Narration Server",
    version="0.13.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===========================================================================
# Kokoro
# ===========================================================================

PIPELINES = {
    "a": KPipeline(
        lang_code="a",
        repo_id="hexgrad/Kokoro-82M",
    ),
    "b": KPipeline(
        lang_code="b",
        repo_id="hexgrad/Kokoro-82M",
    ),
}

DEFAULT_VOICE = "bf_isabella"
SAMPLE_RATE = 24000


def pipeline_for_voice(voice: str) -> KPipeline:
    voice = voice.lower()

    if voice.startswith(("af_", "am_")):
        return PIPELINES["a"]

    if voice.startswith(("bf_", "bm_")):
        return PIPELINES["b"]

    return PIPELINES["b"]


# ===========================================================================
# Voice pools
# ===========================================================================

FEMALE_VOICE_POOL = [
    "af_heart",
    "af_bella",
    "bf_isabella",
]

MALE_VOICE_POOL = [
    "am_michael",
    "bm_george",
    "bm_daniel",
    "am_adam",
    "am_onyx",
]

UNKNOWN_VOICE_POOL = [
    "bm_fable",
    "af_bella",
    "bm_george",
    "af_heart",
]


# ===========================================================================
# Audio / pacing
# ===========================================================================

SILENCE_THRESHOLD = 0.008
EDGE_SAFETY_MS = 18
LEADING_PAD_MS = 15

BASE_TRAILING_PAUSE_MS = {
    "continuation": 35,
    "comma": 90,
    "semicolon": 140,
    "period": 260,
    "question": 290,
    "exclamation": 290,
    "ellipsis": 420,
    "other": 180,
}


# ===========================================================================
# Context and cast state
# ===========================================================================

CONTEXT_LENGTH = 16

CONTEXTS = defaultdict(
    lambda: deque(maxlen=CONTEXT_LENGTH)
)

DIALOGUE_STATE = defaultdict(
    lambda: {
        "quote_open": False,
    }
)


def new_cast_state():
    return {
        "voices": {},
        "gender": {},

        # Recent named entities observed in prose.
        "recent_entities": deque(maxlen=8),

        # Pronoun bindings.
        "pronouns": {
            "she": None,
            "he": None,
        },

        # Speaker inside a quotation that spans several chunks.
        "active_dialogue_speaker": None,

        # Most recent dialogue speakers.
        "last_speaker": None,
        "previous_speaker": None,

        # Distinct speakers involved in the current-ish conversation.
        "participants": deque(maxlen=4),

        # A narration/action sentence may strongly suggest who speaks next.
        "likely_next_speaker": None,

        "female_index": 0,
        "male_index": 0,
        "unknown_index": 0,
    }


CAST_STATE = defaultdict(new_cast_state)


# ===========================================================================
# Attribution vocabulary
# ===========================================================================

ATTRIBUTION_VERBS = (
    "say", "says", "said",
    "ask", "asks", "asked",
    "answer", "answers", "answered",
    "reply", "replies", "replied",
    "add", "adds", "added",
    "continue", "continues", "continued",
    "call", "calls", "called",
    "shout", "shouts", "shouted",
    "whisper", "whispers", "whispered",
    "murmur", "murmurs", "murmured",
    "tell", "tells", "told",
    "cry", "cries", "cried",
    "remark", "remarks", "remarked",
    "observe", "observes", "observed",
    "insist", "insists", "insisted",
    "demand", "demands", "demanded",
    "wonder", "wonders", "wondered",
    "think", "thinks", "thought",
    "exclaim", "exclaims", "exclaimed",
    "respond", "responds", "responded",
    "retort", "retorts", "retorted",
    "announce", "announces", "announced",
    "admit", "admits", "admitted",
    "agree", "agrees", "agreed",
    "protest", "protests", "protested",
    "promise", "promises", "promised",
)

ATTRIBUTION_VERB_PATTERN = (
    r"(?:"
    + "|".join(
        re.escape(word)
        for word in ATTRIBUTION_VERBS
    )
    + r")"
)

ATTRIBUTION_PATTERN = re.compile(
    rf"\b{ATTRIBUTION_VERB_PATTERN}\b",
    re.IGNORECASE,
)


# ===========================================================================
# Name / reference parsing
# ===========================================================================

NAME_STOPWORDS = {
    "A", "An", "And", "Are", "As",
    "Because", "But",
    "Come",
    "Did", "Do", "Does", "Don’t", "Don't",
    "For", "From",
    "Good",
    "He", "Her", "His", "How",
    "I", "If", "In", "Is", "It",
    "My",
    "No", "Not", "Now",
    "Oh", "On", "Our",
    "Please",
    "She", "So",
    "That", "The", "Their", "They", "This",
    "We", "What", "When", "Where", "Which",
    "Who", "Why", "With",
    "Yes", "You", "Your", "You’re", "You're",
    "Test",
}


PRONOUN_SPEAKER_REFERENCES = {
    "i",
    "he",
    "she",
    "they",
    "we",
    "you",
    "him",
    "her",
    "them",
}


NAME_BEFORE_ATTRIBUTION_PATTERN = re.compile(
    rf"\b([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+)"
    rf"\s+{ATTRIBUTION_VERB_PATTERN}\b"
)

ATTRIBUTION_BEFORE_NAME_PATTERN = re.compile(
    rf"\b{ATTRIBUTION_VERB_PATTERN}"
    rf"\s+([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+)\b",
    re.IGNORECASE,
)

LEADING_REFERENCE_ATTRIBUTION_PATTERN = re.compile(
    rf"^\s*(I|he|she|they|we|you)"
    rf"\s+{ATTRIBUTION_VERB_PATTERN}\b",
    re.IGNORECASE,
)

POST_DIALOGUE_REFERENCE_PATTERN = re.compile(
    rf"[”\"']\s*,?\s*"
    rf"(I|he|she|they|we|you)"
    rf"\s+{ATTRIBUTION_VERB_PATTERN}\b",
    re.IGNORECASE,
)

SELF_ATTRIBUTION_PATTERN = re.compile(
    r"\b("
    r"I\s+(?:ask|asked|say|said|tell|told)\s+myself"
    r"|I\s+(?:think|thought)"
    r"|I\s+(?:wonder|wondered)"
    r")\b",
    re.IGNORECASE,
)

LEADING_NAME_PATTERN = re.compile(
    r"^\s*([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+)\b"
)

LEADING_PRONOUN_PATTERN = re.compile(
    r"^\s*(I|She|He|They|We)\b",
    re.IGNORECASE,
)


def valid_name_candidate(candidate: str | None) -> bool:
    if not candidate:
        return False

    if candidate in NAME_STOPWORDS:
        return False

    if candidate.lower() in PRONOUN_SPEAKER_REFERENCES:
        return False

    return True


# ===========================================================================
# Segmentation
# ===========================================================================

def segment_text(text: str) -> list[str]:
    """
    Conservatively split PocketBook chunks into prose units.
    """

    text = text.strip()

    if not text:
        return []

    segments = []
    buffer = []

    length = len(text)
    index = 0

    while index < length:
        char = text[index]
        buffer.append(char)

        # -------------------------------------------------------------------
        # Newline
        # -------------------------------------------------------------------

        if char == "\n":
            candidate = "".join(buffer).strip()

            if candidate:
                segments.append(candidate)

            buffer = []
            index += 1
            continue

        # -------------------------------------------------------------------
        # Sentence boundary
        # -------------------------------------------------------------------

        if char in ".!?":
            lookahead = index + 1

            while (
                lookahead < length
                and text[lookahead] in '”"’\')]}'
            ):
                buffer.append(text[lookahead])
                lookahead += 1

            whitespace_start = lookahead

            while (
                lookahead < length
                and text[lookahead].isspace()
                and text[lookahead] != "\n"
            ):
                lookahead += 1

            if lookahead >= length:
                index = lookahead
                break

            next_char = text[lookahead]

            plausible_next = (
                next_char.isupper()
                or next_char.isdigit()
                or next_char in '“"‘\''
            )

            if (
                plausible_next
                and lookahead > whitespace_start
            ):
                candidate = "".join(buffer).strip()

                if candidate:
                    segments.append(candidate)

                buffer = []
                index = lookahead
                continue

        index += 1

    remainder = "".join(buffer).strip()

    if remainder:
        segments.append(remainder)

    return [
        segment
        for segment in segments
        if segment.strip()
    ]


# ===========================================================================
# Quote parsing
# ===========================================================================

CURLY_OPEN_DOUBLE = "“"
CURLY_CLOSE_DOUBLE = "”"
STRAIGHT_DOUBLE = '"'


def quote_profile(text: str) -> dict:
    return {
        "curly_open_count":
            text.count(CURLY_OPEN_DOUBLE),

        "curly_close_count":
            text.count(CURLY_CLOSE_DOUBLE),

        "straight_count":
            text.count(STRAIGHT_DOUBLE),
    }


def update_quote_state(
    client_id: str,
    text: str,
) -> dict:

    state = DIALOGUE_STATE[client_id]
    was_open = state["quote_open"]

    profile = quote_profile(text)

    curly_open = profile["curly_open_count"]
    curly_close = profile["curly_close_count"]
    straight = profile["straight_count"]

    stripped = text.strip()

    ends_curly_close = stripped.endswith(
        CURLY_CLOSE_DOUBLE
    )

    # -----------------------------------------------------------------------
    # Curly quotes
    # -----------------------------------------------------------------------

    if curly_open or curly_close:

        if was_open:

            # A fresh opening quote while state is already open suggests
            # PocketBook dropped a closing quote upstream.
            if (
                curly_open > 0
                and curly_close < curly_open
            ):
                current_is_dialogue = True
                state["quote_open"] = True
                transition = "dialogue_reopen"

            elif curly_close > curly_open:
                current_is_dialogue = True
                state["quote_open"] = False
                transition = "dialogue_close"

            elif (
                ends_curly_close
                and curly_close >= 1
            ):
                current_is_dialogue = True
                state["quote_open"] = False
                transition = "dialogue_close"

            else:
                current_is_dialogue = True
                state["quote_open"] = True
                transition = "dialogue_continuation"

        else:

            if curly_open > 0:
                current_is_dialogue = True

                if curly_close >= curly_open:
                    state["quote_open"] = False

                    if ATTRIBUTION_PATTERN.search(text):
                        transition = "dialogue_with_attribution"
                    else:
                        transition = "dialogue_complete"

                else:
                    state["quote_open"] = True
                    transition = "dialogue_open"

            elif curly_close > 0:
                current_is_dialogue = True
                state["quote_open"] = False

                if ATTRIBUTION_PATTERN.search(text):
                    transition = "orphan_dialogue_with_attribution"
                else:
                    transition = "orphan_dialogue_close"

            else:
                current_is_dialogue = False
                state["quote_open"] = False
                transition = "outside_dialogue"

    # -----------------------------------------------------------------------
    # Straight quotes
    # -----------------------------------------------------------------------

    elif straight:

        if was_open:
            current_is_dialogue = True

            if straight % 2 == 1:
                state["quote_open"] = False
                transition = "dialogue_close"

            else:
                state["quote_open"] = True
                transition = "dialogue_continuation"

        else:
            current_is_dialogue = True

            if straight % 2 == 0:
                state["quote_open"] = False

                if ATTRIBUTION_PATTERN.search(text):
                    transition = "dialogue_with_attribution"
                else:
                    transition = "dialogue_complete"

            else:
                state["quote_open"] = True
                transition = "dialogue_open"

    # -----------------------------------------------------------------------
    # No visible quote
    # -----------------------------------------------------------------------

    else:

        if was_open:
            current_is_dialogue = True
            state["quote_open"] = True
            transition = "dialogue_continuation"

        else:
            current_is_dialogue = False
            state["quote_open"] = False
            transition = "outside_dialogue"

    return {
        "was_open": was_open,
        "is_dialogue": current_is_dialogue,
        "quote_transition": transition,
        "quote_open_after": state["quote_open"],
        **profile,
    }


# ===========================================================================
# Speaker extraction
# ===========================================================================

def extract_speaker_hint(text: str) -> dict:

    # Proper name before attribution.
    match = NAME_BEFORE_ATTRIBUTION_PATTERN.search(text)

    if match:
        candidate = match.group(1)

        if valid_name_candidate(candidate):
            return {
                "speaker_name": candidate,
                "speaker_reference": None,
            }

    # Proper name after attribution.
    match = ATTRIBUTION_BEFORE_NAME_PATTERN.search(text)

    if match:
        candidate = match.group(1)

        if valid_name_candidate(candidate):
            return {
                "speaker_name": candidate,
                "speaker_reference": None,
            }

    # Pronoun at beginning.
    match = LEADING_REFERENCE_ATTRIBUTION_PATTERN.search(text)

    if match:
        return {
            "speaker_name": None,
            "speaker_reference": match.group(1),
        }

    # Pronoun following quoted dialogue.
    match = POST_DIALOGUE_REFERENCE_PATTERN.search(text)

    if match:
        return {
            "speaker_name": None,
            "speaker_reference": match.group(1),
        }

    if SELF_ATTRIBUTION_PATTERN.search(text):
        return {
            "speaker_name": None,
            "speaker_reference": "I",
        }

    return {
        "speaker_name": None,
        "speaker_reference": None,
    }


# ===========================================================================
# Prose classification
# ===========================================================================

def classify_utterance(
    client_id: str,
    text: str,
) -> dict:

    stripped = text.strip()

    if not stripped:
        return {
            "type": "empty",
            "speaker_name": None,
            "speaker_reference": None,
            "confidence": "high",
        }

    quote_info = update_quote_state(
        client_id,
        stripped,
    )

    speaker_info = extract_speaker_hint(
        stripped
    )

    has_attribution = bool(
        ATTRIBUTION_PATTERN.search(stripped)
    )

    transition = quote_info[
        "quote_transition"
    ]

    if transition == "dialogue_with_attribution":
        utterance_type = "dialogue_with_attribution"
        confidence = "high"

    elif transition == "dialogue_open":
        utterance_type = "dialogue_open"
        confidence = "high"

    elif transition == "dialogue_reopen":
        utterance_type = "dialogue_reopen"
        confidence = "medium"

    elif transition == "dialogue_continuation":
        utterance_type = "dialogue_continuation"
        confidence = "high"

    elif transition == "dialogue_close":
        utterance_type = "dialogue_close"
        confidence = "high"

    elif transition == "dialogue_complete":
        utterance_type = "dialogue"
        confidence = "high"

    elif transition == "orphan_dialogue_close":
        utterance_type = "dialogue_orphan_close"
        confidence = "medium"

    elif transition == "orphan_dialogue_with_attribution":
        utterance_type = "dialogue_orphan_with_attribution"
        confidence = "medium"

    elif has_attribution:
        utterance_type = "possible_attribution"
        confidence = "medium"

    else:
        utterance_type = "narration"
        confidence = "medium"

    return {
        "type": utterance_type,
        "confidence": confidence,
        "has_attribution": has_attribution,
        **speaker_info,
        **quote_info,
    }


# ===========================================================================
# Entity memory
# ===========================================================================

def remember_entity(
    client_id: str,
    name: str,
) -> None:

    if not valid_name_candidate(name):
        return

    state = CAST_STATE[client_id]
    entities = state["recent_entities"]

    # Move repeated entity to most-recent position.
    try:
        entities.remove(name)
    except ValueError:
        pass

    entities.append(name)


def most_recent_entity(
    client_id: str,
    gender: str | None = None,
) -> str | None:

    state = CAST_STATE[client_id]

    for name in reversed(
        state["recent_entities"]
    ):
        known_gender = state[
            "gender"
        ].get(name)

        if gender is None:
            return name

        if known_gender == gender:
            return name

    # If nobody has known gender yet, allow the most recent
    # untyped entity to become the pronoun referent.
    if gender is not None:

        for name in reversed(
            state["recent_entities"]
        ):
            if state["gender"].get(name) is None:
                return name

    return None


def bind_pronoun(
    client_id: str,
    reference: str,
) -> tuple[str | None, str | None]:

    state = CAST_STATE[client_id]
    ref = reference.lower()

    if ref == "i":
        return "__narrator__", None

    if ref in ("she", "her"):

        speaker = state[
            "pronouns"
        ]["she"]

        if speaker is None:
            speaker = most_recent_entity(
                client_id,
                "female",
            )

        if speaker is None:
            speaker = "__she__"

        state["pronouns"]["she"] = speaker
        state["gender"][speaker] = "female"

        return speaker, "female"

    if ref in ("he", "him"):

        speaker = state[
            "pronouns"
        ]["he"]

        if speaker is None:
            speaker = most_recent_entity(
                client_id,
                "male",
            )

        if speaker is None:
            speaker = "__he__"

        state["pronouns"]["he"] = speaker
        state["gender"][speaker] = "male"

        return speaker, "male"

    return None, None


# ===========================================================================
# Conversation state
# ===========================================================================

def add_participant(
    client_id: str,
    speaker: str | None,
) -> None:

    if (
        not speaker
        or speaker.startswith("__unknown")
    ):
        return

    state = CAST_STATE[client_id]
    participants = state["participants"]

    try:
        participants.remove(speaker)
    except ValueError:
        pass

    participants.append(speaker)


def record_dialogue_speaker(
    client_id: str,
    speaker: str | None,
) -> None:

    if not speaker:
        return

    state = CAST_STATE[client_id]

    previous = state["last_speaker"]

    if previous != speaker:
        state["previous_speaker"] = previous

    state["last_speaker"] = speaker
    state["active_dialogue_speaker"] = speaker

    add_participant(
        client_id,
        speaker,
    )


def alternate_speaker(
    client_id: str,
) -> str | None:
    """
    Conservative two-person alternation.
    """

    state = CAST_STATE[client_id]

    participants = list(
        state["participants"]
    )

    # We only trust this when exactly two meaningful participants
    # are currently represented.
    if len(participants) != 2:
        return None

    last = state["last_speaker"]

    if last == participants[0]:
        return participants[1]

    if last == participants[1]:
        return participants[0]

    return None


# ===========================================================================
# Narrative actor inference
# ===========================================================================

def resolve_narrative_actor(
    client_id: str,
    text: str,
) -> str | None:

    stripped = text.strip()

    # Named subject at the beginning:
    #   Mara laughed.
    #   Catherine sat down.
    match = LEADING_NAME_PATTERN.search(
        stripped
    )

    if match:
        candidate = match.group(1)

        if valid_name_candidate(candidate):
            remember_entity(
                client_id,
                candidate,
            )

            return candidate

    # Pronoun subject at beginning:
    #   She laughed.
    #   I grinned.
    match = LEADING_PRONOUN_PATTERN.search(
        stripped
    )

    if match:
        reference = match.group(1)

        speaker, _ = bind_pronoun(
            client_id,
            reference,
        )

        return speaker

    return None


# ===========================================================================
# Retroactive attribution
# ===========================================================================

def previous_context_item(
    client_id: str,
) -> dict | None:

    context = CONTEXTS[client_id]

    if not context:
        return None

    return context[-1]


def retroactively_assign_previous_dialogue(
    client_id: str,
    speaker: str,
    reason: str,
) -> bool:
    """
    We cannot change audio already played, but we CAN repair our
    understanding of the previous utterance for future inference.
    """

    previous = previous_context_item(
        client_id
    )

    if not previous:
        return False

    metadata = previous["metadata"]

    if not metadata.get("is_dialogue"):
        return False

    current_key = metadata.get(
        "speaker_key"
    )

    if current_key not in (
        None,
        "__unknown_dialogue__",
    ):
        return False

    metadata[
        "speaker_key"
    ] = speaker

    metadata[
        "voice_reason"
    ] = (
        "retroactive-" + reason
    )

    metadata[
        "retroactively_resolved"
    ] = True

    record_dialogue_speaker(
        client_id,
        speaker,
    )

    return True


# ===========================================================================
# Observe non-dialogue information
# ===========================================================================

def observe_non_dialogue(
    client_id: str,
    text: str,
    metadata: dict,
) -> None:

    state = CAST_STATE[client_id]

    # -----------------------------------------------------------------------
    # Explicit attribution:
    #   she asked.
    #   Catherine said.
    # -----------------------------------------------------------------------

    if metadata["type"] == "possible_attribution":

        speaker = None

        if metadata.get("speaker_name"):
            speaker = metadata["speaker_name"]

            remember_entity(
                client_id,
                speaker,
            )

        elif metadata.get(
            "speaker_reference"
        ):
            speaker, _ = bind_pronoun(
                client_id,
                metadata[
                    "speaker_reference"
                ],
            )

        if speaker:
            resolved_previous = (
                retroactively_assign_previous_dialogue(
                    client_id,
                    speaker,
                    "attribution",
                )
            )

            # If there was no previous unknown dialogue, this still
            # gives us useful discourse state.
            if not resolved_previous:
                add_participant(
                    client_id,
                    speaker,
                )

            state[
                "likely_next_speaker"
            ] = None

        return

    # -----------------------------------------------------------------------
    # Ordinary narration can reveal characters and action actors.
    # -----------------------------------------------------------------------

    if metadata["type"] != "narration":
        return

    actor = resolve_narrative_actor(
        client_id,
        text,
    )

    metadata[
        "narrative_actor"
    ] = actor

    if not actor:
        return

    add_participant(
        client_id,
        actor,
    )

    # If the immediately preceding dialogue was unknown:
    #
    #   “You're late.”
    #   Mara folds her arms.
    #
    # treat the action actor as a plausible retroactive speaker.
    resolved_previous = (
        retroactively_assign_previous_dialogue(
            client_id,
            actor,
            "following-action",
        )
    )

    if resolved_previous:
        state[
            "likely_next_speaker"
        ] = None

    else:
        # Otherwise action narration is often a good cue for who
        # speaks next:
        #
        #   She laughed.
        #   “That's not reassuring.”
        state[
            "likely_next_speaker"
        ] = actor


# ===========================================================================
# Voice assignment
# ===========================================================================

def next_available_voice(
    pool: list[str],
    narrator_voice: str,
    index: int,
) -> tuple[str, int]:

    if not pool:
        return narrator_voice, index

    for offset in range(
        len(pool)
    ):
        candidate_index = (
            index + offset
        ) % len(pool)

        candidate = pool[
            candidate_index
        ]

        if candidate != narrator_voice:
            return (
                candidate,
                candidate_index + 1,
            )

    return (
        pool[index % len(pool)],
        index + 1,
    )


def assign_cast_voice(
    client_id: str,
    speaker_key: str,
    gender: str | None,
    narrator_voice: str,
) -> str:

    state = CAST_STATE[client_id]

    if speaker_key in state["voices"]:
        return state["voices"][
            speaker_key
        ]

    if gender == "female":

        voice, next_index = (
            next_available_voice(
                FEMALE_VOICE_POOL,
                narrator_voice,
                state["female_index"],
            )
        )

        state[
            "female_index"
        ] = next_index

    elif gender == "male":

        voice, next_index = (
            next_available_voice(
                MALE_VOICE_POOL,
                narrator_voice,
                state["male_index"],
            )
        )

        state[
            "male_index"
        ] = next_index

    else:

        voice, next_index = (
            next_available_voice(
                UNKNOWN_VOICE_POOL,
                narrator_voice,
                state["unknown_index"],
            )
        )

        state[
            "unknown_index"
        ] = next_index

    state["voices"][
        speaker_key
    ] = voice

    if gender:
        state["gender"][
            speaker_key
        ] = gender

    return voice


# ===========================================================================
# Speaker routing
# ===========================================================================

def resolve_dialogue_speaker(
    client_id: str,
    metadata: dict,
) -> tuple[
    str | None,
    str | None,
    str,
    str,
]:
    """
    Returns:
        speaker
        gender
        reason
        confidence
    """

    state = CAST_STATE[client_id]

    # -----------------------------------------------------------------------
    # Explicit proper name
    # -----------------------------------------------------------------------

    speaker_name = metadata.get(
        "speaker_name"
    )

    if speaker_name:

        remember_entity(
            client_id,
            speaker_name,
        )

        gender = state[
            "gender"
        ].get(speaker_name)

        return (
            speaker_name,
            gender,
            "explicit-name",
            "high",
        )

    # -----------------------------------------------------------------------
    # Explicit speaker reference
    # -----------------------------------------------------------------------

    reference = metadata.get(
        "speaker_reference"
    )

    if reference:

        speaker, gender = bind_pronoun(
            client_id,
            reference,
        )

        if speaker:
            return (
                speaker,
                gender,
                "explicit-reference",
                "high",
            )

    # -----------------------------------------------------------------------
    # Quote span continuity
    # -----------------------------------------------------------------------

    if (
        metadata.get("was_open")
        and state[
            "active_dialogue_speaker"
        ]
    ):

        speaker = state[
            "active_dialogue_speaker"
        ]

        return (
            speaker,
            state["gender"].get(
                speaker
            ),
            "dialogue-span-continuity",
            "high",
        )

    # -----------------------------------------------------------------------
    # Narration action cue
    #
    #   Mara shook her head.
    #   “You missed.”
    # -----------------------------------------------------------------------

    if state[
        "likely_next_speaker"
    ]:

        speaker = state[
            "likely_next_speaker"
        ]

        state[
            "likely_next_speaker"
        ] = None

        return (
            speaker,
            state["gender"].get(
                speaker
            ),
            "narrative-action-cue",
            "medium",
        )

    # -----------------------------------------------------------------------
    # Immediately adjacent quote after an explicitly identified
    # dialogue line.
    #
    #   “I'm sure,” I said.
    #   “Mostly.”
    # -----------------------------------------------------------------------

    previous = previous_context_item(
        client_id
    )

    if previous:

        previous_metadata = previous[
            "metadata"
        ]

        previous_speaker = (
            previous_metadata.get(
                "speaker_key"
            )
        )

        if (
            previous_metadata.get(
                "type"
            )
            in (
                "dialogue_with_attribution",
                "dialogue_orphan_with_attribution",
            )
            and previous_speaker
            and previous_speaker
            != "__unknown_dialogue__"
        ):

            return (
                previous_speaker,
                state["gender"].get(
                    previous_speaker
                ),
                "adjacent-dialogue-continuation",
                "medium",
            )

    # -----------------------------------------------------------------------
    # Two-person alternating dialogue
    # -----------------------------------------------------------------------

    alternate = alternate_speaker(
        client_id
    )

    if alternate:

        return (
            alternate,
            state["gender"].get(
                alternate
            ),
            "two-person-alternation",
            "medium",
        )

    # -----------------------------------------------------------------------
    # Graceful fallback
    # -----------------------------------------------------------------------

    return (
        "__unknown_dialogue__",
        None,
        "unknown-dialogue",
        "low",
    )


def choose_voice(
    client_id: str,
    text: str,
    metadata: dict,
    narrator_voice: str,
) -> dict:

    # Non-dialogue always stays narrator, but it can teach
    # the inference system about characters.
    if not metadata.get(
        "is_dialogue"
    ):

        observe_non_dialogue(
            client_id,
            text,
            metadata,
        )

        return {
            "speaker_key":
                "__narrator__",

            "speaker_gender":
                None,

            "selected_voice":
                narrator_voice,

            "voice_reason":
                "narration",

            "speaker_confidence":
                "high",
        }

    speaker, gender, reason, confidence = (
        resolve_dialogue_speaker(
            client_id,
            metadata,
        )
    )

    if (
        speaker == "__narrator__"
        or speaker
        == "__unknown_dialogue__"
    ):

        voice = narrator_voice

    else:

        voice = assign_cast_voice(
            client_id,
            speaker,
            gender,
            narrator_voice,
        )

    if (
        speaker
        and speaker
        != "__unknown_dialogue__"
    ):

        record_dialogue_speaker(
            client_id,
            speaker,
        )

    # Close of quotation ends span continuity but NOT conversation history.
    if not metadata.get(
        "quote_open_after"
    ):
        CAST_STATE[
            client_id
        ][
            "active_dialogue_speaker"
        ] = None

    return {
        "speaker_key":
            speaker,

        "speaker_gender":
            gender,

        "selected_voice":
            voice,

        "voice_reason":
            reason,

        "speaker_confidence":
            confidence,
    }


# ===========================================================================
# Context helpers
# ===========================================================================

def remember_utterance(
    client_id: str,
    text: str,
    metadata: dict,
) -> None:

    CONTEXTS[client_id].append(
        {
            "text": text,
            "metadata": metadata,
        }
    )


def previous_metadata(
    client_id: str,
) -> dict | None:

    context = CONTEXTS[
        client_id
    ]

    if not context:
        return None

    return context[-1][
        "metadata"
    ]


# ===========================================================================
# Logging
# ===========================================================================

def log_context(
    client_id: str,
    text: str,
    metadata: dict,
    segment_number: int,
    segment_count: int,
) -> None:

    context = CONTEXTS[
        client_id
    ]

    print()
    print("=" * 72)
    print("NARRATION CONTEXT")
    print("=" * 72)

    print(
        f"client: {client_id}"
    )

    print(
        f"segment: "
        f"{segment_number}/"
        f"{segment_count}"
    )

    print(
        "quote state before: "
        + (
            "OPEN"
            if metadata.get(
                "was_open"
            )
            else "CLOSED"
        )
    )

    print(
        "quote state after:  "
        + (
            "OPEN"
            if metadata.get(
                "quote_open_after"
            )
            else "CLOSED"
        )
    )

    print()

    if context:

        print(
            "Previous utterances:"
        )

        for index, item in enumerate(
            context,
            start=1,
        ):

            meta = item[
                "metadata"
            ]

            cast = meta.get(
                "speaker_key"
            )

            reason = meta.get(
                "voice_reason"
            )

            cast_text = (
                f" | cast={cast}"
                if cast
                else ""
            )

            reason_text = (
                f" | via={reason}"
                if reason
                else ""
            )

            print(
                f"  {index:02d}. "
                f"[{meta['type']}"
                f"{cast_text}"
                f"{reason_text}] "
                f"{item['text']}"
            )

    else:

        print(
            "Previous utterances: "
            "<none>"
        )

    print()
    print("Current utterance:")

    print(
        f"  text:               "
        f"{text!r}"
    )

    print(
        f"  type:               "
        f"{metadata['type']}"
    )

    print(
        f"  confidence:         "
        f"{metadata['confidence']}"
    )

    print(
        "  speaker name:       "
        + (
            metadata.get(
                "speaker_name"
            )
            or "<unknown>"
        )
    )

    print(
        "  speaker ref:        "
        + (
            metadata.get(
                "speaker_reference"
            )
            or "<none>"
        )
    )

    print(
        "  narrative actor:    "
        + (
            metadata.get(
                "narrative_actor"
            )
            or "<none>"
        )
    )

    print(
        "  cast speaker:       "
        + (
            metadata.get(
                "speaker_key"
            )
            or "<none>"
        )
    )

    print(
        "  cast gender:        "
        + (
            metadata.get(
                "speaker_gender"
            )
            or "<unknown>"
        )
    )

    print(
        "  speaker confidence: "
        + (
            metadata.get(
                "speaker_confidence"
            )
            or "<none>"
        )
    )

    print(
        "  selected voice:     "
        + (
            metadata.get(
                "selected_voice"
            )
            or "<none>"
        )
    )

    print(
        "  routing reason:     "
        + (
            metadata.get(
                "voice_reason"
            )
            or "<none>"
        )
    )

    print(
        "  transition:         "
        + str(
            metadata.get(
                "quote_transition"
            )
        )
    )

    print("=" * 72)
    print()


# ===========================================================================
# Request model
# ===========================================================================

class SpeechRequest(BaseModel):

    text: str = Field(
        min_length=1,
        max_length=20_000,
    )

    voice: str = DEFAULT_VOICE

    speed: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
    )


# ===========================================================================
# Audio helpers
# ===========================================================================

def ms_to_samples(
    milliseconds: float,
) -> int:

    return int(
        milliseconds
        / 1000.0
        * SAMPLE_RATE
    )


def silence(
    milliseconds: float,
) -> np.ndarray:

    return np.zeros(
        ms_to_samples(
            milliseconds
        ),
        dtype=np.float32,
    )


def classify_ending(
    text: str,
) -> str:

    stripped = text.rstrip()

    if not stripped:
        return "other"

    stripped = re.sub(
        r'["”’\'\)\]\}]+$',
        "",
        stripped,
    ).rstrip()

    if stripped.endswith(
        ("...", "…")
    ):
        return "ellipsis"

    if stripped.endswith("?"):
        return "question"

    if stripped.endswith("!"):
        return "exclamation"

    if stripped.endswith("."):
        return "period"

    if stripped.endswith(","):
        return "comma"

    if stripped.endswith(
        (";", ":")
    ):
        return "semicolon"

    if stripped[-1:].isalnum():
        return "continuation"

    return "other"


def choose_trailing_pause(
    text: str,
    metadata: dict | None,
    previous: dict | None,
) -> tuple[int, str]:

    ending = classify_ending(
        text
    )

    base = BASE_TRAILING_PAUSE_MS.get(
        ending,
        BASE_TRAILING_PAUSE_MS[
            "other"
        ],
    )

    if not metadata:
        return base, "baseline"

    transition = metadata.get(
        "quote_transition"
    )

    if transition in (
        "dialogue_open",
        "dialogue_reopen",
        "dialogue_continuation",
    ):

        if ending == "period":
            return (
                190,
                "dialogue-continuation-period",
            )

        if ending == "question":
            return (
                225,
                "dialogue-continuation-question",
            )

        if ending == "exclamation":
            return (
                225,
                "dialogue-continuation-exclamation",
            )

        if ending == "ellipsis":
            return (
                340,
                "dialogue-continuation-ellipsis",
            )

        return (
            min(base, 150),
            "dialogue-continuation",
        )

    if transition in (
        "dialogue_close",
        "dialogue_complete",
        "dialogue_with_attribution",
        "orphan_dialogue_close",
        "orphan_dialogue_with_attribution",
    ):

        if ending == "question":
            return (
                310,
                "dialogue-turn-question",
            )

        if ending == "exclamation":
            return (
                310,
                "dialogue-turn-exclamation",
            )

        return (
            285,
            "dialogue-turn-end",
        )

    if (
        metadata.get("type")
        == "narration"
        and previous
        and previous.get(
            "is_dialogue"
        )
    ):

        return (
            max(base, 320),
            "dialogue-to-narration",
        )

    if (
        metadata.get("type")
        == "possible_attribution"
        and previous
        and previous.get(
            "is_dialogue"
        )
    ):

        return (
            min(base, 205),
            "dialogue-attribution",
        )

    return (
        base,
        "baseline",
    )


def trim_edge_silence(
    audio: np.ndarray,
) -> tuple[
    np.ndarray,
    int,
    int,
]:

    audio = np.asarray(
        audio,
        dtype=np.float32,
    ).squeeze()

    if audio.size == 0:
        return audio, 0, 0

    active = np.flatnonzero(
        np.abs(audio)
        > SILENCE_THRESHOLD
    )

    if active.size == 0:
        return audio, 0, 0

    first_active = int(
        active[0]
    )

    last_active = int(
        active[-1]
    )

    safety = ms_to_samples(
        EDGE_SAFETY_MS
    )

    start = max(
        0,
        first_active - safety,
    )

    end = min(
        audio.size,
        last_active
        + safety
        + 1,
    )

    return (
        audio[start:end],
        start,
        audio.size - end,
    )


def prepare_narration_audio(
    audio: np.ndarray,
    text: str,
    metadata: dict,
    previous: dict | None,
) -> np.ndarray:

    (
        trimmed,
        leading_removed,
        trailing_removed,
    ) = trim_edge_silence(
        audio
    )

    (
        trailing_ms,
        pacing_reason,
    ) = choose_trailing_pause(
        text,
        metadata,
        previous,
    )

    processed = np.concatenate(
        [
            silence(
                LEADING_PAD_MS
            ),
            trimmed,
            silence(
                trailing_ms
            ),
        ]
    )

    print(
        "Narration pacing:"
        f" ending={classify_ending(text)}"
        f" | reason={pacing_reason}"
        f" | removed leading="
        f"{leading_removed / SAMPLE_RATE * 1000:.1f}ms"
        f" | removed trailing="
        f"{trailing_removed / SAMPLE_RATE * 1000:.1f}ms"
        f" | added leading="
        f"{LEADING_PAD_MS}ms"
        f" | added trailing="
        f"{trailing_ms}ms"
    )

    return processed


def float_audio_to_wav(
    audio: np.ndarray,
) -> bytes:

    audio = np.clip(
        audio,
        -1.0,
        1.0,
    )

    pcm_audio = (
        audio * 32767
    ).astype(
        np.int16
    )

    wav_buffer = BytesIO()

    with wave.open(
        wav_buffer,
        "wb",
    ) as wav_file:

        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(
            SAMPLE_RATE
        )

        wav_file.writeframes(
            pcm_audio.tobytes()
        )

    return wav_buffer.getvalue()


# ===========================================================================
# Synthesis
# ===========================================================================

def synthesize_audio(
    text: str,
    voice: str,
    speed: float,
    metadata: dict,
    previous: dict | None,
) -> np.ndarray:

    text = text.strip()

    if not text:
        return np.zeros(
            0,
            dtype=np.float32,
        )

    selected_pipeline = (
        pipeline_for_voice(
            voice
        )
    )

    chunks = []

    generator = selected_pipeline(
        text,
        voice=voice,
        speed=speed,
        split_pattern=r"\n+",
    )

    for _, _, audio in generator:

        chunk = np.asarray(
            audio,
            dtype=np.float32,
        ).squeeze()

        if chunk.size:
            chunks.append(
                chunk
            )

    if not chunks:
        raise RuntimeError(
            "Kokoro produced no audio."
        )

    full_audio = np.concatenate(
        chunks
    )

    return prepare_narration_audio(
        full_audio,
        text,
        metadata,
        previous,
    )


def wav_response(
    wav_bytes: bytes,
) -> Response:

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "Content-Disposition":
                'inline; filename="speech.wav"',

            "Content-Length":
                str(len(wav_bytes)),
        },
    )


# ===========================================================================
# Status endpoints
# ===========================================================================

@app.get("/health")
def health() -> dict:

    return {
        "status": "ready",
        "version": "0.13.0",
        "default_voice": DEFAULT_VOICE,
        "sample_rate": SAMPLE_RATE,

        "internal_segmentation": True,
        "contextual_pacing": True,
        "rolling_context": True,
        "dialogue_state_machine": True,

        "entity_memory": True,
        "pronoun_resolution": True,
        "retroactive_attribution": True,
        "narrative_action_cues": True,
        "dialogue_turn_inference": True,

        "cast_registry": True,
        "role_voice_routing": True,

        "openai_endpoint":
            "/v1/audio/speech",
    }


@app.get("/cast")
def get_cast() -> dict:

    result = {}

    for (
        client_id,
        state,
    ) in CAST_STATE.items():

        result[
            client_id
        ] = {
            "voices":
                dict(
                    state["voices"]
                ),

            "gender":
                dict(
                    state["gender"]
                ),

            "pronouns":
                dict(
                    state["pronouns"]
                ),

            "recent_entities":
                list(
                    state[
                        "recent_entities"
                    ]
                ),

            "participants":
                list(
                    state[
                        "participants"
                    ]
                ),

            "last_speaker":
                state[
                    "last_speaker"
                ],

            "previous_speaker":
                state[
                    "previous_speaker"
                ],

            "likely_next_speaker":
                state[
                    "likely_next_speaker"
                ],

            "active_dialogue_speaker":
                state[
                    "active_dialogue_speaker"
                ],
        }

    return result


@app.get("/context")
def get_context() -> dict:

    result = {}

    for (
        client_id,
        context,
    ) in CONTEXTS.items():

        result[
            client_id
        ] = {
            "quote_open":
                DIALOGUE_STATE[
                    client_id
                ][
                    "quote_open"
                ],

            "utterances":
                list(context),
        }

    return result


@app.post("/context/reset")
def reset_context() -> dict:

    CONTEXTS.clear()
    DIALOGUE_STATE.clear()
    CAST_STATE.clear()

    return {
        "status":
            "cleared",
    }


# ===========================================================================
# Original local endpoint
# ===========================================================================

@app.post("/speak")
def speak(
    request: SpeechRequest,
) -> Response:

    metadata = {
        "type":
            "narration",

        "is_dialogue":
            False,

        "quote_transition":
            "outside_dialogue",
    }

    audio = synthesize_audio(
        text=request.text,
        voice=request.voice,
        speed=request.speed,
        metadata=metadata,
        previous=None,
    )

    return wav_response(
        float_audio_to_wav(
            audio
        )
    )


# ===========================================================================
# OpenAI-compatible endpoint
# ===========================================================================

@app.post(
    "/v1/audio/speech"
)
async def openai_speech(
    request: Request,
) -> Response:

    raw_body = (
        await request.body()
    )

    if not raw_body:
        raise HTTPException(
            status_code=400,
            detail=(
                "Request body was empty."
            ),
        )

    try:
        data = json.loads(
            raw_body
        )

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid JSON: "
                f"{exc}"
            ),
        ) from exc

    text = data.get(
        "input"
    )

    if text is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Missing 'input' field."
            ),
        )

    text = str(text)

    narrator_voice = str(
        data.get(
            "voice",
            DEFAULT_VOICE,
        )
    )

    try:
        speed = float(
            data.get(
                "speed",
                1.0,
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "speed must be numeric."
            ),
        )

    speed = max(
        0.5,
        min(
            speed,
            2.0,
        ),
    )

    client_id = (
        request.client.host
        if request.client
        else "unknown"
    )

    segments = segment_text(
        text
    )

    if not segments:
        raise HTTPException(
            status_code=400,
            detail=(
                "No usable prose "
                "segments found."
            ),
        )

    print()
    print("#" * 72)
    print("POCKETBOOK REQUEST")
    print("#" * 72)

    print(
        "segments detected: "
        f"{len(segments)}"
    )

    print(
        f"original text: "
        f"{text!r}"
    )

    print("#" * 72)

    rendered_segments = []

    try:

        for (
            index,
            segment,
        ) in enumerate(
            segments,
            start=1,
        ):

            previous = (
                previous_metadata(
                    client_id
                )
            )

            metadata = (
                classify_utterance(
                    client_id,
                    segment,
                )
            )

            routing = choose_voice(
                client_id,
                segment,
                metadata,
                narrator_voice,
            )

            metadata.update(
                routing
            )

            log_context(
                client_id,
                segment,
                metadata,
                index,
                len(segments),
            )

            selected_voice = (
                metadata[
                    "selected_voice"
                ]
            )

            selected_language = (
                "American English"
                if selected_voice
                .lower()
                .startswith(
                    ("af_", "am_")
                )
                else
                "British English"
            )

            print("Synthesis:")

            print(
                "  narrator: "
                f"{narrator_voice}"
            )

            print(
                "  voice:    "
                f"{selected_voice}"
            )

            print(
                "  speaker:  "
                f"{metadata.get('speaker_key')}"
            )

            print(
                "  inference:"
                " "
                f"{metadata.get('voice_reason')}"
            )

            print(
                "  confidence:"
                " "
                f"{metadata.get('speaker_confidence')}"
            )

            print(
                "  pipeline: "
                f"{selected_language}"
            )

            print(
                "  speed:    "
                f"{speed}"
            )

            print()

            segment_audio = (
                synthesize_audio(
                    text=segment,
                    voice=selected_voice,
                    speed=speed,
                    metadata=metadata,
                    previous=previous,
                )
            )

            if segment_audio.size:
                rendered_segments.append(
                    segment_audio
                )

            remember_utterance(
                client_id,
                segment,
                metadata,
            )

        if not rendered_segments:
            raise RuntimeError(
                "No segment produced audio."
            )

        full_audio = np.concatenate(
            rendered_segments
        )

        wav_bytes = float_audio_to_wav(
            full_audio
        )

        print(
            "Reassembled "
            f"{len(rendered_segments)} "
            "segment(s)."
        )

        print(
            "Returned "
            f"{len(wav_bytes)} "
            "WAV bytes."
        )

        print()

        return wav_response(
            wav_bytes
        )

    except Exception as exc:

        print(
            "Narration synthesis "
            "failed: "
            f"{exc!r}"
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Speech generation "
                f"failed: {exc}"
            ),
        ) from exc