# The Margin Proscenium Architecture

This document describes the current architecture of the Proscenium development path. The authoritative implementation lives in `scripts/server.py` and `scripts/literary_state.py`; the preserved v0.15 implementation remains the regression baseline for the v0.16 work.

Proscenium began as a multi-voice TTS server, but the architecture is moving toward a more useful distinction: understanding a piece of writing and deciding how to perform it are related problems, not the same problem. The current pipeline can be summarized as:

**text and evidence → literary decision → performance decision → synthesis**

That separation is deliberate. Proscenium may sometimes have enough evidence to make a useful performance choice without having enough evidence to claim that choice as a fact about the book. A character voice can therefore be used tentatively while the underlying speaker remains unresolved. The central rule is simple: **a performance guess must never become literary evidence or literary fact.**

## Request pipeline

Text enters through FastAPI's `/v1/audio/speech` endpoint. The request is validated and segmented using the existing deterministic punctuation and newline rules. Quote tracking and attribution parsing then classify the resulting text as prose, dialogue, or an attribution-bearing segment and collect whatever speaker evidence is available.

For each segment, `prepare_segment` observes relevant actors and other clues, obtains a literary `SpeakerDecision`, then makes a separate `PerformanceDecision`. Prose routes to the narrator. Dialogue with an established speaker can route to that character's voice, while unresolved dialogue may use an established character tentatively when the available evidence supports it. If there is not enough useful performance evidence, it falls back to the narrator.

Kokoro synthesizes the selected performance, after which the rendered segment is recorded in context. Eligible evidence arriving immediately afterward can refine Proscenium's literary understanding of an earlier segment, but it does not retroactively change the audio already produced. The generated audio is concatenated and returned as mono 24 kHz PCM16 WAV.

The v0.16 work is concentrated primarily in the space between parsing the text and choosing the voice. Segmentation, basic pacing, Kokoro synthesis, and WAV generation remain substantially inherited from the v0.15 baseline.

## Literary state and scope

Proscenium owns its literary state. The inference machinery reasons from that state, but it should not become a second hidden memory system or the permanent owner of facts about the book.

`LITERARY_STATES` is currently a process-local map keyed by client IP. Each `LiteraryState` contains a schema version, an opaque `scope_id`, an increasing segment position, scene state, performance state, and bounded recent context. Scene state includes observed entities, pronoun and gender bindings, confirmed conversational participants, turn ordering, quotation state, an active quote decision, and an optional scoped first-person POV identity. Performance state contains cast voices and the existing provisional, locked, and recast information.

Recent context is deliberately bounded. The system currently retains up to 16 context entries, eight recent entities, and four conversational participants rather than allowing runtime memory to grow without limit.

The current scope is a reading-session construct, not a book identity, chapter, detected scene, or claim about a universal protagonist. It is created lazily for a client and replaced after `/context/reset`. Clients sharing an IP still share state, and restarting the server loses that state entirely.

Those limitations matter because durable memory is planned, but persisting today's IP-keyed runtime state verbatim would preserve the wrong abstraction. Before Proscenium remembers a cast across reading sessions, it needs explicit answers to questions such as which book, edition, chapter, scene, and reading scope that memory belongs to.

## Literary decisions and uncertainty

Proscenium distinguishes four literary outcomes:

| Status | Speaker identity | Meaning |
| --- | --- | --- |
| `narration` | none | Prose belongs to the narrator performance |
| `resolved` | character or scoped POV identity | The available evidence establishes the speaker |
| `tentative` | candidate character | Existing inference suggests a speaker but does not establish one |
| `unresolved` | none | The available evidence is insufficient |

`unresolved` is a valid result, not an error condition. Synthesis eventually needs a voice, but that requirement should not force the literary system to pretend it knows something it does not.

Evidence records preserve the source position, text, candidate identity, kind of evidence, and qualitative strength. Confidence is currently categorical rather than a calibrated probability. Explicit attribution takes precedence over weaker clues, while nearby actions and conversational alternation are treated as suggestive evidence rather than proof.

Only resolved dialogue establishes a confirmed conversational participant. Tentative and unresolved turns do not quietly harden themselves into facts merely because the system had to render them somehow.

## First-person narration and dialogue

First-person fiction creates a distinction that the earlier Proscenium architecture blurred. In prose such as:

> I crossed the room.

the first person belongs to the narrative voice. In dialogue such as:

> "Wait," I said.

that same first person is also a character participating in the scene.

Explicit first-person dialogue can therefore create a scoped `pov:<scope_id>` character identity while ordinary first-person prose continues to use the narrator. The scoped POV identity does not automatically acquire a name or gender, does not merge itself with a named character, and does not imply that Proscenium has discovered the protagonist of the book. Ordinary first-person narration and first-person words inside another character's quotation do not create it.

Automatic POV switching is not yet implemented. A future book-level container may eventually reconcile scoped identities across chapters or scenes, but the current architecture deliberately avoids making that claim prematurely.

## Literary decisions versus performance decisions

Literary certainty and useful performance do not need the same threshold. If Proscenium knows that Mara spoke, the literary decision and performance decision can both select Mara. If the evidence merely points toward Mara, however, the literary decision can remain unresolved while the performance layer tentatively chooses Mara's established voice.

That performance choice is recorded separately, with its own speaker, reason, and confidence. It cannot confirm a participant, create literary evidence, alter literary turn history, or teach the inference system that its guess was correct. Literary inference likewise does not inspect cast voices or previous performance guesses when deciding what the book says.

This separation allows later evidence to disagree with an earlier performance choice without contaminating the literary state. The audio may already have been rendered with the wrong voice, but Proscenium is still free to learn the correct answer afterward.

When the available performance clues conflict or point nowhere useful, unresolved dialogue falls back to the narrator. Cast membership alone is never enough to establish that a character is currently present or speaking.

## Later evidence and retrospective refinement

Literature frequently reveals a speaker after the dialogue rather than before it. Proscenium can therefore refine immediately preceding unresolved or tentative dialogue when sufficiently explicit attribution arrives in the following prose.

The current decision-layer gate is intentionally conservative. Bare speech tags such as:

> Mara said.

> I asked.

> said Mara.

can confirm an eligible preceding turn. Thoughts, silence, negation, ordinary actions, and more elaborate prose do not automatically do so. For example:

> I thought about the rain.

> Mara said nothing.

> Mara folded her arms.

may contribute observations about the scene, but none proves that Mara spoke the preceding line.

A later refinement changes Proscenium's understanding of the literary event. It remains separate from the original decision, selected voice, cast metadata, and already-rendered audio. The current system does not search backward across arbitrary intervening prose and does not regenerate earlier audio after learning something new.

## Quotation continuity

Proscenium tracks open quotation spans across incoming segments. If a single quotation is divided into several TTS requests, its identity and uncertainty can continue across those requests rather than treating every fragment as a new speaker turn. An unresolved quotation remains unresolved instead of acquiring a character merely because it continued.

Live v0.16 listening has exposed a related limitation: quotation continuity is not the same thing as document continuity. A human reader uses paragraph boundaries, whitespace, quotation layout, and neighboring prose almost unconsciously when following a conversation. Proscenium currently reasons primarily from the textual fragments delivered to the server and does not have a reliable representation of the original paragraph structure.

We do not yet know how much of that structure survives the PocketBook → Android TTS → Proscenium path. Some of it may already reach the server and be discarded during normalization or segmentation; some may never be supplied by the client. Tracing that boundary is an architectural priority because recovering deterministic structural evidence is preferable to asking increasingly elaborate inference machinery to reconstruct information the ebook already contained.

## Performance and casting

Performance state manages narrator and character voices, provisional or locked assignments, the existing recast behavior, and voice-pool position. A known literary identity can receive a character voice through the existing cast policy, while unresolved dialogue can tentatively borrow an established character performance when the available evidence agrees strongly enough to make that useful.

The current performance system is much better at choosing *which voice* should speak than deciding *how that voice should perform*. Pacing is still comparatively mechanical, expressive direction is limited, and correct speaker routing does not by itself produce natural audiobook acting. Those are separate problems and should remain separate in the architecture rather than being folded into speaker inference.

Live listening has also exposed an important boundary inside a single segment. Consider:

> "You're early," Mara said.

The literary system may correctly identify Mara as the speaker while the synthesis layer sends the entire segment through Mara's voice. A human performance would normally give `"You're early,"` to Mara and `Mara said.` to the narrator. Supporting that requires performance subspans inside a segment rather than another speaker-inference heuristic.

## Character and actor evidence

Narrative action can be useful evidence only when Proscenium has identified a plausible actor. Current heuristics are still capable of treating grammatical subjects or environmental nouns as character candidates. Live testing has produced useful failures in which things such as water or thunder were interpreted as possible actors and allowed to influence nearby dialogue performance.

The lesson is not that action evidence should be abandoned. It is that detecting a nearby grammatical subject and identifying a character are different operations. Stronger entity safeguards are needed before preceding-action evidence can be trusted broadly.

This also reinforces the larger architectural principle: deterministic facts should remain deterministic, while uncertain interpretation should remain visibly uncertain. A capitalized noun near a line of dialogue is evidence to examine, not permission to invent a cast member.

## Lookahead and preflight context

Preflight schema `2` (`deterministic-en-2`) separates source observations and unassessed name candidates from promoted character beliefs. Observations preserve exact spans/locators and quotation context; pronoun proximity does not bind a pronoun or establish gender. Candidate packets bound occurrence/context sampling for future reasoning without invoking a model. Runtime still consults only promoted characters. Schema-1 artifacts must be regenerated; explicit Gutenberg separators remove identifiable boilerplate from analysis without changing document metadata or surviving coarse locators.

`scripts/book_preflight.py` now provides offline EPUB/PDF/TXT/Markdown ingestion and a separately versioned, validated, immutable `BookPreflight` artifact. SHA-256 identifies the source document; coarse text blocks anchor character, alias, and narrow pronoun evidence. Character status remains an inference even when its supporting speech/action observation is deterministic. For the single-reader development workflow, `POST /book/prior` selects one process-wide prior and clears all IP-keyed runtime scopes; new scopes receive that prior regardless of the management client's IP. `GET /book/prior` reads the shared selection, `DELETE /book/prior` clears it and all scopes, and `/context/reset` resets only the caller's scope while retaining the selection. This read-only book evidence lives outside runtime state serialization. It validates known names/variants or suppresses absent weak action candidates without overriding current explicit attribution, seeding scene participants, assigning voices, or synchronizing live requests to source positions. Runtime aliases remain observed identities rather than automatic cast merges.

The current request model is reactive. Proscenium largely reasons from the text that has already arrived when a piece of audio needs to be produced. That works reasonably well for explicit attribution but makes sparse dialogue and later attribution unnecessarily difficult.

A future lookahead layer could analyze more text than is immediately synthesized. At the beginning of a passage, Proscenium might inspect several paragraphs or a larger chapter window for quotation conventions, explicit attribution anchors, POV evidence, paragraph relationships, and probable scene participants. Once playback begins, a rolling lookahead could continue preparing future context while the listener hears already-rendered material.

The desired architecture would still preserve the same evidence discipline:

**lookahead text → observations and evidence → literary state → performance decisions → requested-text synthesis**

Seeing more of the book does not make every interpretation a fact. It simply gives the decision system better evidence.

Whether useful lookahead is possible through the existing ebook/TTS client is still an open question. If the current interface fundamentally supplies only isolated fragments with little document structure, owning more of the reader side may eventually be simpler and more reliable than reconstructing an EPUB through a TTS drinking straw.

## Serialization and future persistence

`LiteraryState.to_json` and `from_json` provide versioned serialization using the existing Pydantic dependency. Loading validates the state envelope and schema version, field types, bounded memories, context ordering, decision roles, evidence scope and positions, and identity-bearing fields. Reserved placeholders, empty identities, malformed identities, and identities belonging to foreign scopes are rejected before routing.

Round trips restore the bounded state and performance/recast information, but these methods do not currently read or write files and are not exposed as persistence endpoints. Older development snapshots that lack required fields are not automatically migrated.

Disk persistence remains intentionally deferred until the ownership and lifetime of literary state are clearer. The storage technology is not the difficult part. Deciding what constitutes a book, reading scope, scene, character identity, POV identity, and reusable performance identity is.

## Diagnostics and failure behavior

`/cast` and `/context` expose development diagnostics for the current state. The original literary decision, any later refinement, and the performance decision are intentionally distinguishable. Voice metadata describes what was actually rendered rather than silently rewriting the literary decision to match it.

Preparation can currently mutate quote, scene, position, pronoun, casting, or recast state before synthesis succeeds. If synthesis later fails, some of those changes may survive even though the failed audio was never added to rendered context. State updates are therefore not transactional.

There is also no general concurrency coordination or overall client/cast eviction. These are known runtime limitations that durable storage and multi-client work will eventually need to address explicitly rather than accidentally preserve.

## Preservation boundaries

The v0.11-derived single-voice path is independent of Proscenium development. Its source files, historical copies, API behavior, and launcher contract should not be changed casually while developing the multi-voice system.

Likewise, deterministic parsing should remain deterministic where the text gives us a definitive answer. Quotation marks, explicit attribution, document structure, and other directly observable evidence should not be handed to a probabilistic model merely because one may eventually exist. Model-assisted or probabilistic inference belongs at genuinely ambiguous decision points.

The long-term architecture is therefore not "put AI in front of Kokoro." It is a layered reading system that preserves what the book actually tells us, reasons carefully about what it does not, and turns that understanding into the best performance it can.
