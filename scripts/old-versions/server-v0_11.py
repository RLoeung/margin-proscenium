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


app = FastAPI(
    title="Local Kokoro TTS",
    version="0.11.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===========================================================================
# Kokoro pipelines
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
# Audio / pacing controls
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
# Rolling narration context
# ===========================================================================

CONTEXT_LENGTH = 12

CONTEXTS = defaultdict(
    lambda: deque(maxlen=CONTEXT_LENGTH)
)

DIALOGUE_STATE = defaultdict(
    lambda: {
        "quote_open": False,
    }
)


# ===========================================================================
# Attribution heuristics
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

NAME_BEFORE_ATTRIBUTION_PATTERN = re.compile(
    rf"\b([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+)"
    rf"\s+{ATTRIBUTION_VERB_PATTERN}\b"
)

ATTRIBUTION_BEFORE_NAME_PATTERN = re.compile(
    rf"\b{ATTRIBUTION_VERB_PATTERN}"
    rf"\s+([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]+)\b",
    re.IGNORECASE,
)

# Pronoun attribution is intentionally stricter than before.
#
# Matches:
#   she asks, ...
#   I say.
#
# but avoids:
#   What makes you ask?
LEADING_REFERENCE_ATTRIBUTION_PATTERN = re.compile(
    rf"^\s*(I|he|she|they|we|you)"
    rf"\s+{ATTRIBUTION_VERB_PATTERN}\b",
    re.IGNORECASE,
)

# Matches:
#   “Yes,” I say.
#   “No,” she replied.
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


# ===========================================================================
# Prose segmentation
# ===========================================================================

def segment_text(text: str) -> list[str]:
    """
    Split a PocketBook request into smaller prose units.

    This is deliberately conservative.

    Strong boundary:
        newline

    Secondary boundary:
        . ? !
        followed by whitespace and something that looks like the start
        of another sentence.

    Closing quotation marks remain attached to the sentence they close.
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

        # ---------------------------------------------------------------
        # Explicit line break
        # ---------------------------------------------------------------

        if char == "\n":
            candidate = "".join(buffer).strip()

            if candidate:
                segments.append(candidate)

            buffer = []
            index += 1
            continue

        # ---------------------------------------------------------------
        # Potential sentence terminator
        # ---------------------------------------------------------------

        if char in ".!?":
            lookahead = index + 1

            # Keep closing quotation/bracket characters with sentence.
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

            # Only split if the following material plausibly begins
            # another sentence or quotation.
            plausible_next_sentence = (
                next_char.isupper()
                or next_char.isdigit()
                or next_char in '“"‘\''
            )

            if (
                plausible_next_sentence
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

    # Defensive cleanup.
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

    ends_with_curly_close = stripped.endswith(
        CURLY_CLOSE_DOUBLE
    )

    # -----------------------------------------------------------------------
    # Directional curly quotes
    # -----------------------------------------------------------------------

    if curly_open or curly_close:

        if was_open:

            # A new opening quotation while we still believe the
            # previous quotation is open is evidence that PocketBook
            # lost a close somewhere upstream.
            if curly_open > 0 and curly_close < curly_open:
                current_is_dialogue = True
                state["quote_open"] = True
                transition = "dialogue_reopen"

            elif curly_close > curly_open:
                current_is_dialogue = True
                state["quote_open"] = False
                transition = "dialogue_close"

            elif (
                ends_with_curly_close
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
                        transition = (
                            "dialogue_with_attribution"
                        )
                    else:
                        transition = "dialogue_complete"

                else:
                    state["quote_open"] = True
                    transition = "dialogue_open"

            elif curly_close > 0:

                current_is_dialogue = True
                state["quote_open"] = False

                if ATTRIBUTION_PATTERN.search(text):
                    transition = (
                        "orphan_dialogue_with_attribution"
                    )
                else:
                    transition = "orphan_dialogue_close"

            else:
                current_is_dialogue = False
                state["quote_open"] = False
                transition = "outside_dialogue"

    # -----------------------------------------------------------------------
    # Straight quote fallback
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
                    transition = (
                        "dialogue_with_attribution"
                    )
                else:
                    transition = "dialogue_complete"

            else:
                state["quote_open"] = True
                transition = "dialogue_open"

    # -----------------------------------------------------------------------
    # No quote characters
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
# Speaker hints
# ===========================================================================

def extract_speaker_hint(text: str) -> dict:

    # -----------------------------------------------------------------------
    # Explicit proper name before attribution
    # -----------------------------------------------------------------------

    match = NAME_BEFORE_ATTRIBUTION_PATTERN.search(
        text
    )

    if match:
        candidate = match.group(1)

        if (
            candidate.lower()
            not in PRONOUN_SPEAKER_REFERENCES
        ):
            return {
                "speaker_name": candidate,
                "speaker_reference": None,
            }

    # -----------------------------------------------------------------------
    # Explicit proper name after attribution
    # -----------------------------------------------------------------------

    match = ATTRIBUTION_BEFORE_NAME_PATTERN.search(
        text
    )

    if match:
        candidate = match.group(1)

        if (
            candidate.lower()
            not in PRONOUN_SPEAKER_REFERENCES
        ):
            return {
                "speaker_name": candidate,
                "speaker_reference": None,
            }

    # -----------------------------------------------------------------------
    # Pronoun attribution at beginning
    # -----------------------------------------------------------------------

    match = LEADING_REFERENCE_ATTRIBUTION_PATTERN.search(
        text
    )

    if match:
        return {
            "speaker_name": None,
            "speaker_reference":
                match.group(1),
        }

    # -----------------------------------------------------------------------
    # Pronoun attribution following dialogue
    # -----------------------------------------------------------------------

    match = POST_DIALOGUE_REFERENCE_PATTERN.search(
        text
    )

    if match:
        return {
            "speaker_name": None,
            "speaker_reference":
                match.group(1),
        }

    # -----------------------------------------------------------------------
    # Self attribution
    # -----------------------------------------------------------------------

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
        ATTRIBUTION_PATTERN.search(
            stripped
        )
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

    elif (
        transition
        == "orphan_dialogue_with_attribution"
    ):
        utterance_type = (
            "dialogue_orphan_with_attribution"
        )
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
# Context
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

    context = CONTEXTS[client_id]

    if not context:
        return None

    return context[-1]["metadata"]


def log_context(
    client_id: str,
    text: str,
    metadata: dict,
    segment_number: int,
    segment_count: int,
) -> None:

    context = CONTEXTS[client_id]

    print()
    print("=" * 72)
    print("NARRATION CONTEXT")
    print("=" * 72)

    print(f"client: {client_id}")

    print(
        f"segment: {segment_number}/{segment_count}"
    )

    print(
        "quote state before: "
        + (
            "OPEN"
            if metadata.get("was_open")
            else "CLOSED"
        )
    )

    print(
        "quote state after:  "
        + (
            "OPEN"
            if metadata.get("quote_open_after")
            else "CLOSED"
        )
    )

    print()

    if context:
        print("Previous utterances:")

        for index, item in enumerate(
            context,
            start=1,
        ):
            meta = item["metadata"]

            speaker_parts = []

            if meta.get("speaker_name"):
                speaker_parts.append(
                    f"name={meta['speaker_name']}"
                )

            if meta.get("speaker_reference"):
                speaker_parts.append(
                    "ref="
                    + meta["speaker_reference"]
                )

            speaker_text = (
                " | " + ", ".join(speaker_parts)
                if speaker_parts
                else ""
            )

            print(
                f"  {index:02d}. "
                f"[{meta['type']}"
                f"{speaker_text}] "
                f"{item['text']}"
            )

    else:
        print(
            "Previous utterances: <none>"
        )

    print()

    print("Current utterance:")
    print(
        f"  text:         {text!r}"
    )
    print(
        f"  type:         "
        f"{metadata['type']}"
    )
    print(
        f"  confidence:   "
        f"{metadata['confidence']}"
    )
    print(
        "  speaker name: "
        + (
            metadata.get("speaker_name")
            or "<unknown>"
        )
    )
    print(
        "  speaker ref:  "
        + (
            metadata.get("speaker_reference")
            or "<none>"
        )
    )
    print(
        "  transition:   "
        + str(
            metadata.get("quote_transition")
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
        ms_to_samples(milliseconds),
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

    ending = classify_ending(text)

    base = BASE_TRAILING_PAUSE_MS.get(
        ending,
        BASE_TRAILING_PAUSE_MS["other"],
    )

    if not metadata:
        return base, "baseline"

    transition = metadata.get(
        "quote_transition"
    )

    # -----------------------------------------------------------------------
    # Continuing dialogue
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Complete dialogue turns
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Dialogue → narration
    # -----------------------------------------------------------------------

    if (
        metadata.get("type") == "narration"
        and previous
        and previous.get("is_dialogue")
    ):
        return (
            max(base, 320),
            "dialogue-to-narration",
        )

    # -----------------------------------------------------------------------
    # Attribution after dialogue
    # -----------------------------------------------------------------------

    if (
        metadata.get("type")
        == "possible_attribution"
        and previous
        and previous.get("is_dialogue")
    ):
        return (
            min(base, 205),
            "dialogue-attribution",
        )

    return base, "baseline"


def trim_edge_silence(
    audio: np.ndarray,
) -> tuple[np.ndarray, int, int]:

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

    first_active = int(active[0])
    last_active = int(active[-1])

    safety = ms_to_samples(
        EDGE_SAFETY_MS
    )

    start = max(
        0,
        first_active - safety,
    )

    end = min(
        audio.size,
        last_active + safety + 1,
    )

    return (
        audio[start:end],
        start,
        audio.size - end,
    )


def prepare_narration_audio(
    audio: np.ndarray,
    text: str,
    metadata: dict | None = None,
    previous: dict | None = None,
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
            silence(LEADING_PAD_MS),
            trimmed,
            silence(trailing_ms),
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
    ).astype(np.int16)

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
# Kokoro synthesis
# ===========================================================================

def synthesize_audio(
    text: str,
    voice: str,
    speed: float,
    metadata: dict | None = None,
    previous: dict | None = None,
) -> np.ndarray:

    text = text.strip()

    if not text:
        return np.zeros(
            0,
            dtype=np.float32,
        )

    selected_pipeline = pipeline_for_voice(
        voice
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
            chunks.append(chunk)

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


def synthesize_wav(
    text: str,
    voice: str = DEFAULT_VOICE,
    speed: float = 1.0,
) -> bytes:

    audio = synthesize_audio(
        text=text,
        voice=voice,
        speed=speed,
    )

    return float_audio_to_wav(
        audio
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
# Health
# ===========================================================================

@app.get("/health")
def health() -> dict:

    return {
        "status": "ready",
        "default_voice":
            DEFAULT_VOICE,
        "sample_rate":
            SAMPLE_RATE,
        "version":
            "0.11.0",

        "narration_processing":
            True,

        "internal_segmentation":
            True,

        "contextual_pacing":
            True,

        "rolling_context":
            True,

        "dialogue_state_machine":
            True,

        "quote_state_recovery":
            True,

        "speaker_reference_tracking":
            True,

        "context_length":
            CONTEXT_LENGTH,

        "american_pipeline":
            True,

        "british_pipeline":
            True,

        "openai_endpoint":
            "/v1/audio/speech",
    }


@app.get("/v1/models")
def models() -> dict:

    return {
        "object": "list",
        "data": [
            {
                "id": "kokoro",
                "object": "model",
                "owned_by": "local",
            }
        ],
    }


# ===========================================================================
# Context inspection / reset
# ===========================================================================

@app.get("/context")
def get_context() -> dict:

    result = {}

    for client_id, context in (
        CONTEXTS.items()
    ):

        result[client_id] = {
            "quote_open":
                DIALOGUE_STATE[
                    client_id
                ]["quote_open"],

            "utterances":
                list(context),
        }

    return result


@app.post("/context/reset")
def reset_context() -> dict:

    CONTEXTS.clear()
    DIALOGUE_STATE.clear()

    return {
        "status": "cleared",
    }


# ===========================================================================
# Original local API
# ===========================================================================

@app.post("/speak")
def speak(
    request: SpeechRequest,
) -> Response:

    try:

        wav_bytes = synthesize_wav(
            text=request.text,
            voice=request.voice,
            speed=request.speed,
        )

        return wav_response(
            wav_bytes
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Speech generation failed: "
                f"{exc}"
            ),
        ) from exc


# ===========================================================================
# OpenAI-compatible API
# ===========================================================================

@app.post("/v1/audio/speech")
async def openai_speech(
    request: Request,
) -> Response:

    raw_body = await request.body()

    if not raw_body:
        raise HTTPException(
            status_code=400,
            detail="Request body was empty.",
        )

    try:
        data = json.loads(
            raw_body
        )

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "Request body is not valid JSON: "
                f"{exc}"
            ),
        ) from exc

    if not isinstance(
        data,
        dict,
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "JSON request must be an object."
            ),
        )

    text = data.get("input")

    if text is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error":
                    "Missing required 'input' field.",
                "received":
                    data,
            },
        )

    text = str(text)

    voice = str(
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
            detail="speed must be numeric.",
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
                "No usable prose segments "
                "were found."
            ),
        )

    print()
    print("#" * 72)
    print("POCKETBOOK REQUEST")
    print("#" * 72)
    print(
        f"segments detected: "
        f"{len(segments)}"
    )
    print(
        f"original text: {text!r}"
    )
    print("#" * 72)

    rendered_segments = []

    try:

        for index, segment in enumerate(
            segments,
            start=1,
        ):

            previous = previous_metadata(
                client_id
            )

            metadata = classify_utterance(
                client_id,
                segment,
            )

            log_context(
                client_id,
                segment,
                metadata,
                index,
                len(segments),
            )

            selected_language = (
                "American English"
                if voice.lower().startswith(
                    ("af_", "am_")
                )
                else "British English"
            )

            print("Synthesis:")
            print(
                f"  voice:    {voice}"
            )
            print(
                f"  pipeline: "
                f"{selected_language}"
            )
            print(
                f"  speed:    {speed}"
            )
            print()

            segment_audio = synthesize_audio(
                text=segment,
                voice=voice,
                speed=speed,
                metadata=metadata,
                previous=previous,
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
            f"Reassembled "
            f"{len(rendered_segments)} "
            f"segment(s)."
        )

        print(
            f"Returned "
            f"{len(wav_bytes)} "
            f"WAV bytes."
        )

        print()

        return wav_response(
            wav_bytes
        )

    except Exception as exc:

        print(
            "Narration synthesis failed: "
            f"{exc!r}"
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Speech generation failed: "
                f"{exc}"
            ),
        ) from exc