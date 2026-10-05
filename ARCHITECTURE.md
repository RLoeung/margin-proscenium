# Current architecture: Proscenium v0.16 development

Authoritative implementation: `scripts/server.py` and `scripts/literary_state.py`. The preserved v0.15 implementation is the regression baseline. This first v0.16 slice separates literary identity, inference evidence, and performance; it does not implement durable storage.

## Request pipeline

1. FastAPI/Pydantic validate `/v1/audio/speech`; segment input using unchanged deterministic punctuation/newline rules.
2. Quote tracking classifies dialogue versus prose; attribution matching excludes quoted speech and extracts speaker hints.
3. `prepare_segment` observes actors, obtains a `SpeakerDecision`, and selects a voice. Prose and unresolved dialogue use the requested narrator voice; character dialogue uses the existing cast policy.
4. Kokoro synthesizes each segment; unchanged trimming and punctuation/dialogue pauses shape pacing.
5. `record_segment` appends the rendered entry and attaches eligible later evidence/refinements. Audio is concatenated and returned as mono 24 kHz PCM16 WAV.

## State and scope

`LITERARY_STATES` is a process-local map keyed by client IP. Each `LiteraryState` contains:

- Schema version 1, an opaque `scope_id`, and an increasing segment position.
- `SceneState`: observed entities, gender/pronoun bindings, confirmed participants, turn ordering, quote state, active quote decision, and an optional scoped POV entity.
- `PerformanceState`: cast voices, provisional/locked/recast status, and voice-pool indices.
- Up to 16 `ContextEntry` records containing classification, original decision, selected voice/cast metadata, optional later refinement, and following-action evidence. Recent entities retain eight entries and participants four.

The initial scope is created lazily for a client and replaced on `/context/reset`. It is a runtime reading scope, not a book identifier or detected chapter/scene. Explicit first-person attribution creates `pov:<scope_id>` only when needed. This identifies the first-person speaker within that scope; it does not identify a protagonist, establish a name/gender, or merge with a named character. Ordinary first-person prose and first-person words inside another character's quotation do not create it.

Automatic POV switching and chapter/scene boundaries are not implemented. A future book container can own multiple scoped states and explicitly reconcile identities/casting; this slice does not assert a work-wide POV identity. Clients sharing an IP still share state.

`to_json`/`from_json` provide versioned serialization using the existing Pydantic dependency. Loading checks the envelope/version, field types, memory bounds, decision roles, context ordering, evidence scope/positions, and every identity-bearing field, including entity history, map keys, context actors and attribution names. Identities must use the current parser's named-entity spelling shape or reference the registered `pov:<scope_id>`; reserved placeholders, empty identities, and malformed/foreign scoped identities are rejected before routing. Scope tokens start with an ASCII letter/digit and otherwise contain letters, digits, underscores or hyphens. Round trips restore bounded deques and performance/recast data. These methods do not read/write files and are not exposed as import/export endpoints. Restart still loses state; there is no old-state migration.

## Decisions and evidence

| Status | Speaker identity | Meaning |
| --- | --- | --- |
| `narration` | null | Prose performance; may contain attribution observations |
| `resolved` | Character or scoped POV ID | Explicit named/first-person attribution, or continuation of it |
| `tentative` | Candidate character | Existing third-person pronoun/recency inference, or continuation of it |
| `unresolved` | null | No sufficiently established identity; a valid outcome |

Confidence is categorical, not a calibrated probability. Immutable evidence records include kind, source scope/position/text, candidate, and qualitative strength. An original decision holds at most four evidence records; an entry may also retain one immediately following action observation.

Explicit attribution takes precedence. Open-quote continuity preserves identity **and uncertainty**, including unresolved identity. Nearby action and two-person alternation supply suggestive evidence only; neither forces a character assignment. Action evidence is eligible only at the immediately adjacent position. There is no pending next-speaker assignment.

Only resolved dialogue adds confirmed participants. Tentative/unresolved turns break known turn ordering. Narrator fallback creates no speaker, POV entity, participant, or cast assignment. Third-person bindings retain existing heuristic gender/recast behavior, but their speaker decisions remain tentative; comprehensive coreference and trait-evidence redesign are deferred.

Immediately following explicit attribution prose can refine unresolved/tentative dialogue. A conservative decision-layer gate requires a bare subject/verb speech tag such as `Mara said.`, `I asked.`, or `said Mara.`; it does not change general attribution parsing. Thoughts, silence, negation and tags with complements/modifiers do not qualify. For example, `I thought about the rain.`, `Mara thought about the rain.` and `Mara said nothing.` remain observations and cannot confirm a preceding speaker. This deliberately leaves some valid but elaborated tags unresolved. The refinement stays separate from the original decision, `cast_speaker`, selected voice, and cast metadata. It cannot override already resolved attribution. Following action attaches evidence only. Neither mechanism searches backward across unrelated prose or regenerates audio.

## Boundaries and diagnostics

- **Parser:** deterministic segmentation, quote transitions, attribution surface and classification remain in `server.py`.
- **Literary state:** typed models and serialization live in `literary_state.py`, which imports no synthesis/model code. Proscenium owns state.
- **Decision inference:** returns explicit decisions/evidence. Orchestration and legacy pronoun updates remain in the server; this is not yet a pure independent decision service.
- **Performance:** narrator/cast selection is separate from literary identity; pools, narrator avoidance, and one provisional recast are retained.
- **Synthesis:** Kokoro adapter, WAV encoding, and two import-time pipelines are unchanged.
- **HTTP:** request schema/endpoints are unchanged. `/cast` and `/context` add scope/schema and decision diagnostics. The `likely_next_speaker` field remains null for diagnostic compatibility. Context `cast_speaker` means original literary identity (null for narration/unresolved speech); consult `refinement` for later attribution.

Preparation can advance quote/scene/cast state and position before synthesis fails; earlier rendered segments may remain in context after a later failure. State changes are not transactional, and there is no concurrency coordination or overall client/cast eviction. Durable storage must address these contracts rather than persist IP-keyed mutable state blindly.

## Preservation boundaries

Quoted attribution words must not create tags; grammatical starters must not create cast entities. Deterministic parsing, quote spans, prose narrator routing, voice/recast policy, reset behavior, WAV format, and pacing remain unchanged except for the approved literary-routing changes above.

Single-voice v0.11 remains independent and untouched, including filenames, historical copies, API and launcher behavior. Book identity, disk storage, model-based inference, POV switching, alias merging, and retrospective audio regeneration remain future work.
