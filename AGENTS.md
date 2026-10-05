# Working on The Margin Proscenium

- Inspect repository docs and current code before work; do not require prompts to restate project history. Start with Git status and README; `ARCHITECTURE.md` is the architecture guide, `DEVELOPMENT.md` the runtime/check/preservation guide, and `ROADMAP.md` the direction.
- **Proscenium** means the multi-voice development path in `scripts/server.py`, baseline v0.15.0. **Single-voice** means stable v0.11 narration in `scripts/server_v0_11.py`. **Literary state** means entities/scene/cast/reading memory; **decision inference** resolves ambiguous evidence; **performance** means voice/pacing realization.
- Do not casually modify single-voice logic while developing Proscenium. Preserve both hyphen/underscore v0.11 files, historical snapshots, extension releases and the BAT selector contract unless the user explicitly scopes a change.
- Preserve deterministic parsing where evidence is definitive. Persistence belongs to Proscenium, not the decision model. Do not implement roadmap items merely because they appear in documentation.
- Run `.\.venv\Scripts\python.exe -B -m unittest discover -s scripts -p test_server_routing.py -v` from the root for Proscenium behavior changes. Add focused regression coverage for affected behavior; existing tests do not cover the whole pipeline.
- Do not broadly discover/import `test_kokoro.py`; it synthesizes and writes output at import time. Model startup/synthesis is not required for the routing suite. Report execution blockers honestly.
- Review staged files and diff before commits. Keep environments, generated audio, credentials and downloaded models out of Git. Preserve verified baseline hashes and avoid dependency upgrades or broad refactors outside the task scope.
