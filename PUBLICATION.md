# Publication and Distribution

This document records the publication boundary for **The Margin Proscenium**, including project identity, source licensing, third-party dependencies, and the distinction between the public source repository and any future packaged release.

It is intentionally conservative. The current repository is safe to publish as source under the boundaries described here, but that does not automatically make every possible installer, bundled executable, model package, or redistributable build safe to ship without another review.

## Project identity

**The Margin Proscenium** is a Red Work Atelier project.

Copyright (c) 2026 Red Work Atelier

The public repository is hosted at `RLoeung/margin-proscenium`. Repository-local Git authorship uses the Red Work Atelier identity rather than a personal development identity.

The Red Work Atelier name, project names, logos, and other branding are separate from the source-code license. The Mozilla Public License grants rights to the covered software; it does not grant trademark or branding rights.

## Source license

Original project software in this repository is licensed under the **Mozilla Public License 2.0 (MPL-2.0)**. The repository contains the unmodified MPL 2.0 license text in `LICENSE`, with project attribution and additional notices in `NOTICE`.

MPL 2.0 is a file-level copyleft license. Modifications to MPL-covered source files remain subject to the MPL when distributed, while the license can coexist with separately licensed files and dependencies. The MPL's secondary-license mechanism remains available under the terms of the license.

This document is a project record, not legal advice.

## What the public repository contains

The public repository is intended to contain the project's own source, tests, documentation, historical source material that is appropriate to preserve, launcher material, and environment metadata useful for development.

It does **not** intentionally publish the working virtual environment, downloaded model caches, generated audio, local configuration, temporary files, machine backups, or personal development credentials. `.gitignore` and the publication audit are intended to keep those boundaries explicit rather than relying on memory each time the repository is updated.

The repository's source-only publication status should not be interpreted as a finished end-user distribution. The current development environment has not been reconstructed and validated as a clean-machine installation, and the project does not yet provide a general-user installer.

## Dependencies and distribution

The development environment relies on separately installed third-party software and separately downloaded model assets. Those components retain their own licenses and are not relicensed by the MPL merely because Proscenium uses them.

Current dependency and asset notes include:

- **Kokoro 0.9.4** — Apache-2.0.
- **Misaki 0.9.4** — Apache-2.0.
- **hexgrad/Kokoro-82M** model repository — Apache-2.0. Model weights, configuration, and voice assets are downloaded separately and are not tracked in this repository. Exact revision and model-card attribution should be recorded before redistributing those assets.
- **PyTorch** — BSD-3-Clause for the main project, with its accompanying third-party notices.
- **Transformers** — Apache-2.0.
- **spaCy** and the currently used `en_core_web_sm` model — MIT.
- **FastAPI** and **Pydantic** — MIT.
- **Uvicorn**, **Starlette**, **NumPy**, and **SoundFile** — permissive BSD-family licensing for the main Python projects.
- **libsndfile**, used beneath SoundFile, has LGPL obligations that become relevant if its native library is bundled in a future distribution.
- **espeakng-loader 0.2.4** — MIT for the loader package. Its wheel includes eSpeak NG native binaries/data, so a future bundled release needs to preserve the provenance and licensing obligations of those included components.
- **eSpeak NG / phonemizer-related components** introduce GPL-family licensing considerations. Misaki's phonemization path loads eSpeak functionality in-process, which deserves specific review before an integrated binary or installer is distributed.

The important boundary is that using these dependencies during development is not the same legal event as redistributing them inside one packaged product. A source repository that instructs users to install compatible dependencies separately has a different distribution surface from an installer that ships Python, native libraries, models, voices, and all runtime dependencies together.

## Model and voice assets

Kokoro model and voice assets are currently downloaded outside Git and remain outside the project's tracked source. This is deliberate both for repository size and because model redistribution should be reviewed independently from source-code publication.

Before Red Work Atelier redistributes model weights, voices, configuration, or cached Hugging Face assets, the project should pin the exact upstream revision being shipped, preserve the relevant model-card and license attribution, and confirm that every included asset is covered by the expected terms. Do not assume that a repository-level license automatically describes every file obtainable from that repository or cache.

## Future packaged releases

A future installer, standalone executable, container, appliance, or other integrated distribution needs a fresh dependency and licensing review based on what is actually included in that artifact.

That review should identify bundled Python packages, native DLLs or shared libraries, model and voice assets, required license texts and notices, source-offer or source-availability obligations where applicable, and any interaction between MPL-covered project files and GPL/LGPL components. It should also verify the exact provenance and versions of the shipped artifacts rather than relying only on the current development environment snapshot.

Packaging decisions should therefore follow the architecture and deployment plan. There is no need to solve hypothetical installer licensing before the project knows what it intends to bundle, but there is also no reason to assume the current source-publication review automatically covers that future artifact.

## Publication hygiene

The repository history was rewritten before initial public publication to use the Red Work Atelier identity and remove historical personal email and Windows username information from the public branch. Private local recovery history may still exist outside the public repository and should remain private.

Future publication checks should focus on the actual diff and repository state rather than repeating the original audit from scratch. Before pushing significant new material, verify that generated audio, models, caches, credentials, machine-specific secrets, private backups, and unrelated personal files have not entered the tracked tree.

Machine-specific development paths may appear in development documentation when they are necessary to describe the preserved environment. They should not be presented as required installation paths for future users.

## Current publication status

The current project boundary is suitable for a **public source repository** under MPL 2.0, with third-party dependencies and model assets remaining separately licensed and, where noted above, separately downloaded.

That conclusion applies to the source repository as presently structured. It should be revisited when Red Work Atelier begins distributing model assets, native dependencies, an installer, a bundled runtime, or another integrated end-user release.
