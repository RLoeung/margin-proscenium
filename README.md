# The Margin Proscenium

Local Kokoro literary narration with two intentional runtime paths:

- **Proscenium:** `scripts/server.py`, v0.15.0 deterministic multi-voice literary performance; current development line.
- **Single-voice:** `scripts/server_v0_11.py`, v0.11.0 stable narration. Preserve its behavior independently of Proscenium development.

## Run

PowerShell, using the existing environment (one server at a time):

```powershell
Set-Location C:\AI\kokoro
# Proscenium
.\.venv\Scripts\python.exe -m uvicorn scripts.server:app --host 0.0.0.0 --port 8282
# Single-voice
.\.venv\Scripts\python.exe -m uvicorn scripts.server_v0_11:app --host 0.0.0.0 --port 8282
```

The unchanged selector is preserved at `launchers/Launch_Kokoro_TTS_Server_v2.bat`. The user's original remains in Downloads. Both use the commands above; the underscore filename is required by this launcher contract. Launch commands were verified against the BAT; live synthesis was not exercised during foundation work.

Safe routing tests:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s scripts -p test_server_routing.py -v
```

## Map

- `scripts/`: both runtime paths and tests; `old-versions/`: v0.11/v0.12/v0.13 historical sources.
- `extension/`: Isabella Reader source and release ZIPs, retained as project history.
- `launchers/`: preserved BAT selector.
- `requirements-environment.txt`: full installed-package snapshot, not a validated reconstruction lock.
- `.venv/`, `output/`, `voices/`: local environment, generated audio, downloaded voices; excluded from Git.

Read `ARCHITECTURE.md` for current behavior and seams, `DEVELOPMENT.md` for runtime/preservation details, and `ROADMAP.md` for direction. `AGENTS.md` gives concise agent working rules.

The archived extension targets port 5150 `/speak` with `text`; this launcher uses 8282. Proscenium only accepts speech at `/v1/audio/speech` with `input`; single-voice retains `/speak`. No compatibility changes were made.
