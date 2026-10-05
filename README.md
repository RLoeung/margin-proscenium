# The Margin Proscenium

The Margin Proscenium is an experimental **Red Work Atelier** system for turning ordinary ebook text into a locally generated reading performance.

The aim is to give a story a recognizable narrator and cast: distinguish narration from dialogue, follow characters and conversations, and give characters persistent voices as a book unfolds.

Instead of requiring a professionally produced audiobook, the ebook itself becomes the source for the performance.

That larger goal is still taking shape. The current version can separate passages, identify many dialogue cues, infer speakers, assign voices, and adjust pauses. It can also misread a scene or choose the wrong speaker. Its character memory currently lasts only while the server is running; remembering a book and its cast between reading sessions is planned work.

## Two ways to listen

**Proscenium — experimental multi-voice reading**

The active development path uses different voices for narration and characters. It looks for clues in the writing to decide who is speaking. Development of v0.16 is underway from the preserved v0.15 baseline.

**Single-voice narration — the stable sibling**

For a simpler reading experience, the v0.11-derived single-voice path reads through one narrator voice. It remains an intentional part of the project, separate from Proscenium’s experiments with casting and speaker inference.

The existing development launcher lets you choose between the two.

## Can I actually use this?

The development setup works, but this is **not yet a download-and-listen application**.

There is no validated installer for ordinary users or packaged Proscenium release, and installation on a fresh computer has not been verified. Using the current version requires technical setup before you can connect an ebook reader and start listening.

If you are comfortable working with a Python development environment, [DEVELOPMENT.md](DEVELOPMENT.md) describes the existing setup and launch commands. Otherwise, the repository currently offers a view of the project’s progress rather than a finished reading app.

Older browser-extension files are preserved here, but they do not work unchanged with the current Proscenium setup. User-facing installation, usage examples, listening samples, and release instructions will be added as those workflows are ready.

## Why “Proscenium”?

A proscenium is the theatrical frame around a stage—the opening through which an audience sees the performance.

Here, the software sits between the words of a book and their spoken performance. It works toward giving narration, dialogue, voices, and pauses a place on that stage.

## What “local” means

Speech is generated on your computer using Kokoro, the speech engine underneath the project. The current narration code does not send book text to a hosted speech-generation service.

Software and voice/model files may still need to be downloaded, and ‘local’ should not be taken as a blanket privacy guarantee. The current setup is intended for a trusted local environment rather than as an internet-facing service.

## For the technically curious

- [Development](DEVELOPMENT.md): current environment, launch commands, tests, and known setup limitations.
- [Architecture](ARCHITECTURE.md): how the current reading pipeline works.
- [Roadmap](ROADMAP.md): what comes next.
- [Agent guidance](AGENTS.md): working context for Codex sessions.
- [Publication and distribution](PUBLICATION.md): repository boundaries and third-party licensing considerations.

## Stewardship and license

The Margin Proscenium is a **Red Work Atelier** project.

Copyright (c) 2026 Red Work Atelier

Original project software is licensed under the [Mozilla Public License 2.0](LICENSE); see [NOTICE](NOTICE). Third-party dependencies and downloaded models retain their own licenses. The [distribution notes](PUBLICATION.md#dependencies-and-distribution) explain the distinction and the checks needed before a future bundled release.
