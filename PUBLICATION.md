# Publication boundary

Reviewed 2026-10-04. The Margin Proscenium is stewarded by **Red Work Atelier**. Public author and committer: `Red Work Atelier <5330732+RLoeung@users.noreply.github.com>`, configured only in this repository. Both unpublished commits were rewritten to use that identity and generic Windows user paths; their order, messages, dates and runtime contents were preserved. Private recovery history remains outside the repository. Publish the reviewed `main` branch, not a mirror of local application refs or recovery data.

## Repository boundary

The repository contains project source, tests, historical sources, launcher, dependency inventory and four small extension source ZIPs (17 text members). No vendored dependency source, dependency binaries, weights or voice tensors were identified. The largest historical source is about 59 KB. No embedded credentials or private service URLs were found in the source/archive review.

`.venv/`, generated audio/output, Python/tool/model caches, local environment files, scratch files and backups are excluded from Git. The verified preservation directory is outside the repository. Generic `C:\AI\kokoro` paths remain because they document the unchanged BAT contract; the extension's older localhost endpoint remains documented in DEVELOPMENT.md. Recheck staged files and history before publication.

## Project license

Copyright (c) 2026 Red Work Atelier

Original project software is licensed under [Mozilla Public License 2.0](LICENSE), including the project-owned historical source and extension source archives. The standard license text is unmodified. The root [NOTICE](NOTICE) applies Exhibit A to those files without altering preserved source bytes. No Exhibit B incompatibility designation is applied. Third-party software and assets retain their own licenses.

MPL 2.0 is appropriate for the current source-only repository. Its file-level copyleft preserves changes to covered files while permitting larger works under other terms. [Mozilla's combination guidance](https://www.mozilla.org/en-US/MPL/2.0/combining-mpl-and-gpl/) explains the Section 3.3 route for GPL-family combinations; this is not a blanket clearance for an eventual packaged executable.

## Dependencies and distribution

Installed metadata/license files were checked against upstream terms where relevant. The environment snapshot is not a bundled distribution or a validated clean-machine installer.

| Component used by this project | Reviewed terms / boundary |
| --- | --- |
| [Kokoro](https://github.com/hexgrad/kokoro/blob/main/LICENSE) 0.9.4 and [Misaki](https://github.com/hexgrad/misaki/blob/main/LICENSE) 0.9.4 | Apache-2.0; separately installed Python code. |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | Model repository declares Apache-2.0; weights/config/voices download separately. No such assets are tracked. Pin and review exact asset revisions and preserve model-card attribution before redistribution. |
| [eSpeak NG](https://github.com/espeak-ng/espeak-ng/blob/master/COPYING), phonemizer-fork 3.3.2 | GPLv3-family terms; installed phonemizer identifies GPLv3-or-later. Misaki imports phonemizer and loads the eSpeak DLL through ctypes in-process. This is not an isolated command-line subprocess boundary. |
| [espeakng-loader](https://github.com/thewh1teagle/espeakng-loader/blob/main/LICENSE) 0.2.4 | Upstream loader code is MIT; its installed wheel includes eSpeak DLL/data, which are not made MIT by the loader license. Exact bundled native build/source provenance remains a packaging check. |
| [PyTorch](https://github.com/pytorch/pytorch/blob/main/LICENSE) 2.13.0 | Main project BSD-3-Clause; installed wheel declares additional Apache/LLVM-exception, BSD-2, BSL and MIT components. Preserve its full notices if distributing wheels/binaries. |
| [Transformers](https://github.com/huggingface/transformers/blob/main/LICENSE) 5.14.1 | Apache-2.0, separately installed. |
| [spaCy](https://github.com/explosion/spaCy/blob/master/LICENSE) 3.8.14 and en_core_web_sm 3.8.0 | MIT in installed package/model license files; installed separately. |
| [FastAPI](https://github.com/fastapi/fastapi/blob/master/LICENSE) 0.140.6 and [Pydantic](https://github.com/pydantic/pydantic/blob/main/LICENSE) 2.13.4 | MIT, separately installed. |
| Uvicorn, Starlette, NumPy, SoundFile | BSD-3-Clause main licenses; NumPy wheel includes other permissive components. SoundFile's native [libsndfile](https://libsndfile.github.io/libsndfile/FAQ.html) uses LGPL terms; retain native notices and satisfy applicable relinking/source obligations if bundled. |

Installing GPL dependencies separately does not relicense the project's original source automatically. It also does not establish that distributing an integrated executable is exempt from GPL obligations: the in-process phonemizer/eSpeak interaction needs a combined-work review. MPL 2.0 retains its secondary-license mechanism rather than unnecessarily replacing the project's license now.

Before an installer or bundled Release: inventory exact binaries, native data and model/voice revisions; retain all licenses/notices and model-card credits; resolve GPL corresponding-source/build obligations and MPL Section 3.3 treatment for any combined work; satisfy LGPL obligations; review PyTorch/NumPy native components and any GPU runtime terms. No such bundle is approved by this review. Existing extension ZIPs contain project source only, not these dependencies.

## Publication status

Ready for a public source repository on the reviewed boundary. No GitHub repository, remote, push or release has been created. Clean installation and listening validation remain future development/distribution work; README makes no claim that they are complete.
