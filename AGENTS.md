# AGENTS.md

This repository is the working source for **The Margin Proscenium**, a Red Work Atelier project. Treat the repository itself as durable project memory: read the relevant current code and documentation before proposing or implementing changes rather than relying on a large prompt to restate the architecture.

## Start here

Before modifying Proscenium, read:

- `README.md` for the human-facing project description.
- `ARCHITECTURE.md` for current system boundaries and invariants.
- `ROADMAP.md` for current priorities and intentionally deferred work.
- `DEVELOPMENT.md` for the environment, test commands, preserved baselines, and workflow.
- The relevant implementation and tests for the task at hand.

Check Git status before editing. Do not assume the working tree is clean, committed, pushed, or merged.

## Architectural boundaries

Proscenium separates three concerns:

**text and observations → literary decision → performance decision → synthesis**

Literary state records what the system has observed or inferred about the text, including entities, dialogue identity, POV, quotation state, scene/conversation context, evidence, and uncertainty.

Performance state records how the text is rendered, including cast voices and provisional, locked, or recast voice assignments. A performance decision may make a useful tentative rendering choice even when the literary speaker remains unresolved.

The hard invariant is:

**A performance guess must never become literary evidence or literary fact.**

Do not allow voice assignment, cast membership, or a previous performance guess to confirm a speaker, establish a literary participant, or feed back into literary inference. Literary uncertainty is allowed to remain unresolved even when synthesis requires a voice.

Deterministic evidence should remain deterministic. Do not introduce probabilistic or model-assisted reasoning for facts that can be recovered directly from text, punctuation, attribution, document structure, or other observable source information.

## Scope discipline

Prefer the smallest coherent change that advances the current architecture. Avoid combining unrelated cleanup, refactoring, persistence, inference, reader work, performance work, and packaging into one implementation slice.

Unless a task explicitly includes them, do not casually change:

- segmentation behavior;
- pacing;
- Kokoro synthesis or WAV handling;
- dependencies;
- persistence;
- scene detection;
- probabilistic or LLM inference;
- reader/client architecture;
- distribution or packaging.

Do not silently solve an out-of-scope problem because it is nearby. Record it as a limitation or roadmap item when appropriate.

## Single-voice preservation

The v0.11-derived single-voice path is an independent stable path, not an earlier version of Proscenium that should automatically inherit new architecture.

Do not modify `scripts/server_v0_11.py`, its preserved historical copies, or the single-voice launcher contract unless the task explicitly requires it. Maintenance or compatibility fixes may be appropriate when deliberately scoped, but Proscenium casting, literary inference, and state machinery should not leak into the single-voice path by default.

## State and uncertainty

`unresolved` is a valid literary result, not an error that must be eliminated. Do not manufacture certainty merely because synthesis needs to continue.

First-person fictional narration and first-person dialogue are related but distinct performance roles. Scoped POV identities must remain scope-bound unless later architecture explicitly establishes a broader identity. Do not infer a universal protagonist, name, gender, or cross-book identity from first-person usage alone.

Action cues and conversational alternation are evidence, not proof. Narrative grammatical subjects are not automatically characters. Be especially cautious about allowing weak actor extraction to create new performance candidates.

State serialization exists as an architectural boundary, not yet as durable storage. Do not add disk persistence until the ownership and lifetime of book, chapter, scene, POV, character, and performance state have been deliberately established.

## Tests and validation

Use the routing suite documented in `DEVELOPMENT.md` for Proscenium changes. Add focused regression tests for changed behavior and run the full routing suite before reporting a slice complete.

Also run `git diff --check` and review the diff for scope creep, unintended feedback from performance state into literary state, and accidental changes to the single-voice path.

Automated routing tests are not listening acceptance. If a change affects audible routing, segmentation, casting, pacing, or performance behavior, identify the need for live listening validation rather than treating passing unit tests as proof that the result sounds correct.

Do not use broad test discovery that imports the manual synthesis script `scripts/test_kokoro.py`.

## Documentation

Human-readable documentation is authored deliberately rather than generated as implementation exhaust.

Do not broadly rewrite or “polish” `README.md`, `ARCHITECTURE.md`, `ROADMAP.md`, or other explanatory prose unless the task explicitly asks for documentation writing. When an implementation changes a documented fact, make the smallest factual update needed or report the documentation delta for the human author to incorporate.

`DEVELOPMENT.md` may receive narrow factual maintenance when commands, tests, environment facts, or runtime behavior change. `PUBLICATION.md` should remain a conservative record of publication, licensing, dependency, and distribution boundaries.

Keep `AGENTS.md` concise and architectural. It is instruction and project memory for development agents, not a changelog.

## Working style

Inspect before editing. Preserve known-good behavior unless the task explicitly changes it. Prefer evidence over inference, explicit uncertainty over invented certainty, and regression tests over confidence.

When a task is complete, report:

- files changed;
- tests and validation performed;
- remaining known limitations relevant to the slice;
- whether live listening is still required;
- whether the working tree remains uncommitted.

Do not commit, push, merge, publish, or begin another implementation slice unless explicitly instructed.
