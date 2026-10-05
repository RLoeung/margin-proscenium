# Development and preservation

## Runtime recipe

Windows x64; existing venv: `C:\AI\kokoro\.venv`. Runtime verified as CPython 3.12.10 (MSC v.1943 AMD64). The base interpreter is a per-user Python installation; its exact local path is recorded in the untracked `.venv/pyvenv.cfg`.

Important installed versions: Kokoro 0.9.4, Misaki 0.9.4, FastAPI 0.140.6, Uvicorn 0.51.0, Pydantic 2.13.4, NumPy 2.5.1, Torch 2.13.0, SoundFile 0.14.0, spaCy 3.8.14, en_core_web_sm 3.8.0, Transformers 5.14.1, huggingface_hub 1.25.1, espeakng-loader 0.2.4.

`requirements-environment.txt` records `python -B -m pip freeze --all`, including transitive/tool packages and the spaCy model URL/hash. It preserves installed distributions without upgrades. It is not a curated minimal dependency list or a fully reproducible lock. Do not replace the existing environment until a separate reconstruction is validated.

Known gaps: no validated clean installation; most wheels lack recorded hashes/index provenance; OS/native dependencies and model caches are not archived; Kokoro model/config/voice downloads use an unpinned `hexgrad/Kokoro-82M` revision. The absolute venv base path is machine-specific. Synthesis and v0.11 runtime were not smoke-tested in this task.

## Launcher contract

Original: the user's Downloads folder, `Launch_Kokoro_TTS_Server_v2.bat`. Exact copy: `launchers/Launch_Kokoro_TTS_Server_v2.bat`. It changes directory to `C:\AI\kokoro`, uses the existing venv, binds `0.0.0.0:8282`, and selects one of these commands:

```powershell
Set-Location C:\AI\kokoro
.\.venv\Scripts\python.exe -m uvicorn scripts.server:app --host 0.0.0.0 --port 8282
.\.venv\Scripts\python.exe -m uvicorn scripts.server_v0_11:app --host 0.0.0.0 --port 8282
```

The BAT explicitly requires the underscore module name. All three v0.11 source copies are hash-identical and retained. The archived extension calls `127.0.0.1:5150/speak` with `text`; Proscenium uses `/v1/audio/speech` with `input`, and the launcher uses 8282. Preserve this known mismatch until a scoped compatibility task.

## Checks and workflow

From the project root:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s scripts -p test_server_routing.py -v
```

Foundation verification: all 10 routing tests passed on 2026-10-04. Restricted execution initially failed to launch the base interpreter; an approved execution outside that restriction succeeded. No code workaround was needed. Tests stub Kokoro and do not synthesize or download models.

Never use broad discovery that imports `test_kokoro.py`: it synthesizes and writes WAVs at import time. Routing tests cover attribution, entity/pronoun safeguards, quote continuity and action cues, but bypass actual voice assignment and HTTP orchestration. Before related behavioral work, add focused coverage for segmentation, voice/recast policy, API/reset, pacing and failure handling; obtain explicit listening acceptance for audio changes.

Read README/architecture/roadmap and inspect current code, check Git status, create a development branch, make focused changes, run scoped tests, inspect the staged diff, then commit. Keep runtime source changes separate from foundation updates. Do not casually change single-voice narration while developing Proscenium.

## Preserved baseline

Before foundation edits, 19 project files (source/tests, historical sources, extension source/releases and two output WAVs) were copied and SHA-256 verified at `C:\AI\kokoro-preservation-20261004-pre-v016`. The external BAT was subsequently copied and verified there too. `SHA256-MANIFEST.csv` records all 20 files. No venv, bytecode or model cache was duplicated. Empty `voices/` contained no files.

Original `server.py` SHA-256:
`9495581AC2E3AFD05AEA6F554FEAD63E000F01B1EEA0D3AE48A14CEB1378B5F2`

Each original v0.11 copy SHA-256:
`5BEC1C342D338DCD21A48F22FDD11FC5E681FA0CED137AC4A379A113B48B11C4`

The initial Git checkpoint preserves pre-v0.16 source plus foundation documentation, dependency inventory and launcher. Generated output is backed up separately and ignored by Git. No runtime source or test behavior was changed.

## Dependency and distribution boundary

Direct runtime imports require Kokoro, NumPy, FastAPI and Pydantic; the launcher requires Uvicorn. The manual smoke script additionally imports SoundFile. Kokoro brings further dependencies and model assets. The snapshot includes transitive/tool packages and is not a minimal requirements declaration. The captured development environment passed routing tests; clean-machine installation and fresh end-to-end synthesis remain unverified.

`C:\AI\kokoro` is the preserved development layout, not a required installation location for future users. The BAT remains machine-specific and unchanged. Its `0.0.0.0` binding listens on all interfaces; these unauthenticated development servers should not be exposed to untrusted networks. This is not a public hosting recipe.

## Source map

- `scripts/server.py`: Proscenium; `scripts/server_v0_11.py`: selected single-voice module; hyphenated v0.11 copies remain preserved.
- `scripts/old-versions/`: historical sources; `test_server_routing.py`: safe routing suite; `test_kokoro.py`: manual synthesis script.
- `extension/`: source and historical ZIPs; `launchers/`: preserved selector.
- `.venv/`, `output/`, `voices/`: ignored environment/audio/model data.

Baseline commit: `1efb79d05bb1cbbc97f4474640d9c6ba56d4f21f`. Publication boundary and outstanding privacy/license decisions live in [PUBLICATION.md](PUBLICATION.md); documentation cleanup does not remove personal metadata from earlier commits.
