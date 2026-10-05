# The Margin Proscenium

The Margin Proscenium is a local, Kokoro-based text-to-speech project for reading literary prose aloud. It combines narration, dialogue voices and pacing to explore how a story can be performed rather than simply spoken.

Two listening paths are available in the current development setup:

- **Proscenium (v0.15):** the active multi-voice path, using deterministic rules to identify dialogue, infer speakers and assign voices.
- **Single-voice narration (v0.11-derived):** the stable sibling path for straightforward narration, kept separate from Proscenium's casting and inference work.

## Can I use it?

This is an early local development project. It runs in an existing Windows/Python environment, but installation and distribution are not yet polished for general users. There is no installer or packaged release, and setup on a clean machine has not been verified. Model assets may need downloading before local synthesis can run.

For technically comfortable users evaluating the source, [DEVELOPMENT.md](DEVELOPMENT.md) records the current environment, exact launch commands and limitations. The existing BAT selector chooses either runtime path. Archived browser-extension releases are retained as history; their endpoint/port does not match the current Proscenium setup.

User-facing installation, usage examples, screenshots/audio demonstrations and release instructions will be added as those workflows are validated. A project license has not yet been selected.

## Project information

- [Architecture](ARCHITECTURE.md): current processing and boundaries.
- [Development](DEVELOPMENT.md): runtime recipe, source map, tests and preservation records.
- [Roadmap](ROADMAP.md): current direction.
- [Agent guidance](AGENTS.md): durable working context for Codex sessions.
- [Publication review](PUBLICATION.md): repository boundary and remaining publication decisions.
