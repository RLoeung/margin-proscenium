# Publication boundary

Review date: 2026-10-04. Source publication preparation only; no remote, push, installer, release packaging or license adoption performed.

## Audit findings

- **Private/identity decision:** the original commit includes the configured personal Git author/committer name and email; its DEVELOPMENT.md includes Windows username paths. Current documentation generalizes those paths, but history still contains them. The owner chose to keep local history and decide public identity before publishing. Do not silently rewrite the preserved baseline.
- **Harmless machine-specific material:** the unchanged BAT uses `C:\AI\kokoro` and port 8282; historical extensions use localhost port 5150. These document actual runtime contracts, not secrets. Port/API mismatches are recorded in DEVELOPMENT.md.
- **Legitimate project record:** both runtime paths, tests, older source, extension source and four release ZIPs remain. The ZIPs contain 17 small text source/documentation members, not model or executable payloads. The largest existing tracked file is about 59 KB; the largest ZIP is about 3 KB.
- No embedded passwords, API keys, authentication tokens or private service URLs were identified by tracked-text and archive-content inspection. This is a bounded repository review, not a guarantee against every possible secret pattern.

## Included and excluded

Git tracks project source, tests, documentation, launcher and dependency inventory. `.gitignore` excludes venvs, Python/tool caches, generated output, model artifacts, local environment files, scratch/backups and editor artifacts. Existing `.venv/`, `output/` and `scripts/__pycache__/` are untracked. The verified preservation directory is a sibling outside the Git root and remains outside publication. `.env.example` is allowed only with non-secret placeholders. Review tracked files, ignored files, history and ZIP contents again before a future push; ignore rules do not remove already tracked content.

## Licensing decision

Recommend [MIT](https://opensource.org/license/mit) for original project code: a short permissive license allowing use, modification and redistribution with notices retained. No LICENSE file is added until the owner chooses the license and copyright attribution.

This recommendation does not relicense dependencies or establish compatibility for a future bundled distribution. [Kokoro code](https://github.com/hexgrad/kokoro/blob/main/LICENSE) and [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) identify Apache-2.0 licensing. Verify exact model/voice revisions and notices, the spaCy model, and all dependency obligations. In particular, [eSpeak NG](https://github.com/espeak-ng/espeak-ng/blob/master/COPYING) has GPL terms; review its use through the phonemizer/loader chain and implications for any combined or bundled distribution before promising MIT-only distribution. Confirm ownership/provenance of project and extension source. No dependencies/model assets are bundled in this repository.

## Before publication

Resolve historical identity disclosure, choose the project license/attribution and complete applicable third-party checks. Clean-machine setup and listening validation remain development/distribution gaps, not claims made by the README. No GitHub action is authorized by this document.
