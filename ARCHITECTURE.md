# Current architecture: Proscenium v0.15

Authoritative implementation: `scripts/server.py`. This describes current behavior, not a completed v0.16 design.

## Request pipeline

1. FastAPI/Pydantic validate `/v1/audio/speech`; segment input using deterministic punctuation/newline rules.
2. Quote tracking classifies dialogue versus prose; attribution matching excludes quoted speech and extracts speaker hints.
3. Narrative actor observation updates entity/pronoun evidence; deterministic inference selects a speaker and categorical confidence/reason.
4. Prose uses the requested narrator voice. Dialogue receives a cast voice, selected from gender/unknown pools with narrator avoidance where possible.
5. Kokoro synthesizes each segment; amplitude-based edge trimming and punctuation/dialogue pauses shape pacing.
6. Context is appended and scene evidence updated, including retrospective attribution. Audio is concatenated and returned as mono 24 kHz PCM16 WAV.

## State and decisions

`CONTEXTS`, `DIALOGUE_STATE`, and `CAST_STATE` are process-local maps keyed by client IP. Context retains 16 entries, recent entities eight, and participants four. Cast mappings/client maps have no overall eviction policy. Restart loses state; clients sharing an IP share state.

Inference prioritizes explicit names/references, open-quote continuity, narrative action cues, two-participant alternation, then conservative narrator fallback. Unknown-gender voices are provisional; reliable gender binding permits one recast before locking. Confidence is a label, not a calibrated probability.

Scene updates can retrospectively change an earlier context speaker, but do not regenerate earlier audio or fully reconcile its recorded voice metadata. State changes are not transactional with synthesis, and shared mutable state has no explicit concurrency coordination.

## Boundaries and seams

- **Parser:** segmentation, quote transitions, attribution surface and classification. Preserve deterministic results when evidence is definitive.
- **Literary state:** entities, pronouns, scene/cast memory and reading-session identity. Future persistence belongs to Proscenium.
- **Decision inference:** consume observations/state and return decisions with evidence/confidence; future probabilistic work addresses ambiguity.
- **Performance:** narrator/cast voice policy and pacing, distinct from literary speaker identity.
- **Synthesis:** Kokoro pipeline adapter and WAV encoding. Two pipelines currently initialize at import time.
- **HTTP:** request validation, client identification, `/health`, `/cast`, `/context`, `/context/reset`, and `/v1/audio/speech` orchestration.

## Preservation invariants

- Keep v0.15 deterministic parsing and routing as the regression baseline before changing semantics.
- Quoted attribution words alone must not create speaker tags; grammatical starters must not create cast entities.
- Preserve quote-span continuity, narrator routing for prose, stable voice assignment/recast rules, reset semantics, WAV format and pacing until an explicit behavioral change is approved and tested.
- Single-voice v0.11 is an independent product path; preserve its logic, API, filenames and launcher behavior.
- First-person dialogue currently maps to narrator identity. Separating those identities is future work, not a foundation change.
