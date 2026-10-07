# Developing The Margin Proscenium

This document describes the current development environment, safe workflow, preserved baselines, and known runtime limitations for The Margin Proscenium.

For how the system itself is organized, see [ARCHITECTURE.md](ARCHITECTURE.md). For where the project is headed, see [ROADMAP.md](ROADMAP.md).

## Current environment

The preserved development environment is Windows x64 using the virtual environment at:

`C:\AI\kokoro\.venv`

The verified interpreter is CPython 3.12.10 (MSC v.1943 AMD64). The base interpreter is a per-user Python installation, and its exact local path is recorded in the untracked `.venv/pyvenv.cfg`.

Important installed versions include:

- Kokoro 0.9.4
- Misaki 0.9.4
- FastAPI 0.140.6
- Uvicorn 0.51.0
- Pydantic 2.13.4
- NumPy 2.5.1
- Torch 2.13.0
- SoundFile 0.14.0
- spaCy 3.8.14
- en_core_web_sm 3.8.0
- Transformers 5.14.1
- huggingface_hub 1.25.1
- espeakng-loader 0.2.4

`requirements-environment.txt` records the output of `python -B -m pip freeze --all`, including transitive and tooling packages and the spaCy model URL/hash. It preserves the known-working installed environment without upgrading anything, but it is not a curated minimal dependency list or a fully reproducible lock file.

Do not replace the existing environment until reconstruction has been tested separately.

## Reproducibility gaps

A clean installation has not yet been validated. Most wheels do not have recorded hashes or index provenance, OS and native dependencies are not archived, and model caches are not part of the repository. Kokoro model, configuration, and voice downloads currently use an unpinned `hexgrad/Kokoro-82M` revision.

The virtual environment also retains a machine-specific base interpreter path. For now, the captured environment is the reference runtime rather than a promise that the project can be recreated byte-for-byte on another computer.

## Starting the server

The preserved launcher is:

`launchers/Launch_Kokoro_TTS_Server_v2.bat`

It changes to `C:\AI\kokoro`, uses the existing virtual environment, binds to `0.0.0.0:8282`, and lets the user select either Proscenium or the stable single-voice path.

Proscenium:

```
Set-Location C:\AI\kokoro
.\.venv\Scripts\python.exe -m uvicorn scripts.server:app --host 0.0.0.0 --port 8282
```

Single-voice:

```
Set-Location C:\AI\kokoro
.\.venv\Scripts\python.exe -m uvicorn scripts.server_v0_11:app --host 0.0.0.0 --port 8282
```

The BAT requires the underscore module name for v0.11. All three preserved v0.11 source copies are hash-identical and intentionally retained.

The historical browser extension uses an older interface at `127.0.0.1:5150/speak` with a `text` field. Current Proscenium uses `/v1/audio/speech` with `input`, while the development launcher uses port 8282. That mismatch is known and should remain preserved until compatibility work is explicitly scoped.

Proscenium prints its current application version when the server starts, using the canonical `app.version` value rather than maintaining a second version string.

## Running the routing tests

From the repository root:

```
.\.venv\Scripts\python.exe -B -m unittest discover -s scripts -p test_server_routing.py -v
```

Do **not** use broad test discovery that imports `test_kokoro.py`. That file is a manual synthesis script and writes WAV output at import time.

The routing suite stubs Kokoro before importing the server, so it can exercise routing and API behavior without downloading models or performing live synthesis. The suite covers the shared preparation and recording path, cast assignment and recast behavior, first-person narration versus scoped POV dialogue, unresolved identity, quotation continuity, tentative inference, action and alternation evidence, literary versus performance decisions, serialization, diagnostics, reset behavior, malformed-state rejection, synthesis failures, and API/WAV behavior.

The current v0.16 working tree passes 42 routing tests and 24 focused preflight tests. Passing those tests is not listening acceptance. A routing decision can be internally correct and still produce an incoherent or unpleasant reading, so changes that affect audible behavior should also be tested through live synthesis and actual listening.

## Offline book preflight

Generate a reusable artifact without loading Kokoro:

```
.\.venv\Scripts\python.exe -B -m scripts.book_preflight book.epub -o book.preflight.json
```

EPUB, PDF, UTF-8/UTF-16 TXT, and basic prose Markdown (`.md`) are supported. EPUB ingestion uses package spine order and preserves headings/paragraphs; PDF preserves page boundaries. PDF ingestion alone requires the optional `pypdf>=6,<7` dependency in `requirements-preflight.txt` (`python -m pip install -r requirements-preflight.txt`). The preserved environment snapshot is unchanged. Scanned/image-only and encrypted PDFs are unsupported; extraction warnings report missing text pages and layout uncertainty.

Run the focused tests separately, without importing the manual synthesis script:

```
.\.venv\Scripts\python.exe -B -m unittest discover -s scripts -p test_book_preflight.py -v
```

For the single-reader development workflow, `POST /book/prior` selects one active prior for the whole server process, with the artifact as the JSON body. `GET /book/prior` exposes that same selection from any client IP without creating runtime state; management responses identify `attachment_scope: "server"`. Attachment/replacement clears all client literary/performance scopes, and every newly created IP-keyed scope receives the read-only active prior. `DELETE /book/prior` removes the selection and clears all scopes. `/context/reset` clears only the caller's runtime scope and retains the selected prior. Apply book changes while playback is stopped; the existing lack of concurrency coordination remains. The selection is process-local and disappears on restart. No source-position matching or reader changes are required. Programmatic callers can use `preflight_document(path)`, `BookPreflight.from_json(value)`, and `LiteraryState.for_book(prior)`.

The immutable artifact retains extracted text blocks, document SHA-256/metadata, likely characters, aliases, supporting excerpts/spans, categorical confidence, and observation/inference provenance. It is an offline document artifact, separate from ordinary runtime state serialization; runtime round trips do not restore the attachment. An attached prior validates unambiguous known names/variants and suppresses absent weak action candidates, while explicit runtime attribution remains authoritative. Priors do not seed scene presence, pronoun bindings, gender locks, or voices. Alias lookup validates observed spellings; it does not merge runtime cast identities. Discovery uses conservative English speech/action rules; absence is not proof that a character does not exist. Gender evidence is retained only for narrow named-subject reflexive constructions and is not promoted into runtime fact.

Validation on 2026-10-06 used the bundled CPython 3.12.14 with `.venv/Lib/site-packages` on `PYTHONPATH`, because the preserved virtual environment's base Python executable was missing. The environment was not rebuilt. PDF tests used the bundled pypdf installation; fresh installation of the optional dependency in the preserved environment remains unverified.

## Development workflow

Before changing Proscenium, read the current README, Architecture, and Roadmap, inspect the code involved, and check Git status. The repository is intended to carry enough architectural memory that a new Codex task should be able to recover the project from the repository rather than requiring a giant prompt that retells its history.

Keep development slices focused. Add regression coverage for behavior being changed, run the routing suite, run `git diff --check`, and inspect the resulting diff for unrelated changes or architectural feedback paths. Unless a task explicitly includes it, verify that the single-voice path remains untouched.

Live listening belongs in the workflow whenever a change affects routing, casting, segmentation, pacing, or other audible behavior. Commit only after the relevant automated and listening checks have reached an appropriate checkpoint.

## Live listening and diagnostics

Automated tests establish invariants. Listening tests establish whether those invariants produce a coherent reading.

When comparing independent literary scenarios, restart Proscenium if clean state is required. State is currently process-local and keyed by client IP, so requests from the same client can intentionally influence later requests until the server is restarted or the context is reset.

When examining v0.16 diagnostics, keep four things distinct: the original literary decision, any later literary refinement, the performance decision, and the voice that was actually rendered. An unresolved literary speaker may legitimately have a tentative performance speaker. The important invariant is that the tentative performance choice must not feed back into literary state as evidence or fact.

Human listening has already exposed problems that routing tests alone could not make obvious, including missing document/paragraph context, mixed dialogue-and-attribution segments being rendered through one voice, and overly permissive actor detection. These are tracked as architectural and roadmap work rather than being treated as failures of the test suite.

## Current state behavior

State currently lives only in memory and is keyed by client IP. `scripts/literary_state.py` provides schema-versioned JSON round trips, but there are no state files, persistence endpoints, or restart recovery. Reset creates a new scope on the next access, and scope IDs do not currently identify books, chapters, or automatically detected scenes.

State updates are also not transactional. Preparation can advance position, quotation, POV, entity, pronoun, participant, casting, or recast state before synthesis completes. If synthesis later fails, some of those mutations may survive even though the failed audio was never added to rendered context.

There is no general concurrency coordination or overall client/cast eviction yet. Durable storage will need to address these ownership and failure semantics rather than simply serializing the current IP-keyed mutable state to disk.

## Preserved baseline

Before the v0.16 foundation work, 19 project files were copied and SHA-256 verified at:

`C:\AI\kokoro-preservation-20261004-pre-v016`

The external BAT was subsequently copied and verified there as well. `SHA256-MANIFEST.csv` records all 20 files. The preservation copy does not include the virtual environment, bytecode, model caches, or empty `voices/` content.

Original `server.py` SHA-256:

`9495581AC2E3AFD05AEA6F554FEAD63E000F01B1EEA0D3AE48A14CEB1378B5F2`

Each original v0.11 copy SHA-256:

`5BEC1C342D338DCD21A48F22FDD11FC5E681FA0CED137AC4A379A113B48B11C4`

Baseline commit:

`1efb79d05bb1cbbc97f4474640d9c6ba56d4f21f`

The public history was rewritten before publication to use the Red Work Atelier identity and remove historical personal paths. The preserved runtime file hashes above did not change.

## Source map

- `scripts/server.py` — Proscenium orchestration, parsing, inference, routing, and synthesis integration.
- `scripts/literary_state.py` — typed literary/performance state and the serialization boundary.
- `scripts/server_v0_11.py` — selected stable single-voice module.
- `scripts/old-versions/` — historical server sources.
- `scripts/test_server_routing.py` — safe routing regression suite.
- `scripts/test_kokoro.py` — manual synthesis script.
- `extension/` — historical browser-extension source and releases.
- `launchers/` — preserved server selector.
- `.venv/`, `output/`, `voices/` — ignored environment, generated audio, and model/voice data.

## Network and distribution boundary

`C:\AI\kokoro` is the preserved development layout, not a required installation location for future users. The BAT remains machine-specific.

The development launcher binds to `0.0.0.0`, which listens on all interfaces. These are unauthenticated development servers and should not be exposed to untrusted networks. This setup is not a public hosting recipe.

The environment snapshot also should not be mistaken for a distribution package. Clean-machine installation, dependency reconstruction, model revision pinning, and fresh end-to-end synthesis remain unvalidated. For licensing, dependency boundaries, and future packaging requirements, see [PUBLICATION.md](PUBLICATION.md).
