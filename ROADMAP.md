# The Margin Proscenium Roadmap

Proscenium is moving from a multi-voice TTS experiment toward a reading system that can build and use an understanding of a book while it performs it. The destination is not simply “guess a speaker and pick a voice.” The larger path is:

**ebook structure → literary understanding → performance decisions → spoken reading**

The roadmap is intentionally ordered around information and architecture before clever inference. If the ebook already contains useful evidence, Proscenium should preserve and use it before asking a probabilistic model to reconstruct what was thrown away.

## Current epoch: v0.16 literary and performance state

The v0.16 work is establishing the boundaries that later reasoning and persistence will depend on. Proscenium now has explicit literary state, can distinguish narration, resolved, tentative, and unresolved speaker decisions, and separates first-person character dialogue from narrator performance.

Action cues and conversational alternation are evidence rather than automatic speaker assignments. Literary decisions and performance decisions are also separate: an unresolved literary speaker can receive a tentative performance voice without turning that performance guess into a fact about the book. Versioned in-memory state serialization now provides a boundary for future persistence, although state still disappears when the server stops.

This architecture is deliberately being stabilized before adding durable storage or a probabilistic decision model.

## Immediate priority: recover the structure of the book

Live listening exposed a more fundamental problem than weak speaker inference: Proscenium may not be receiving enough of the ebook's structure.

Human readers do not infer dialogue from words and punctuation alone. Paragraph boundaries, whitespace, quotation layout, neighboring prose, and whether two sentences belong to the same paragraph all help establish who is speaking and whether a line continues an existing turn. The current reader-to-server path often presents Proscenium with small sequential requests, and it is not yet clear how much of the original document topology survives that journey.

The next investigation should trace the text before normalization and segmentation and determine what the current PocketBook → Android TTS → Proscenium path actually supplies. In particular, we need to know whether paragraph breaks, newlines, leading or trailing whitespace, markup, utterance grouping, or other useful structure reaches the server and is discarded there, or whether it never reaches Proscenium at all.

That distinction matters. If the information is already present, preserve it. If the client removes it, increasingly elaborate inference on the server is the wrong fix.

## Near term: make better use of deterministic evidence

Several problems revealed by listening tests should be addressed before introducing model-assisted inference.

### Paragraph and document continuity

If source paragraph information is available, represent it explicitly as evidence. Dialogue continuing inside the same paragraph can strongly suggest speaker continuity, while a paragraph boundary can signal a turn change or a new structural relationship.

This should remain literary evidence rather than an absolute universal rule. Authors are allowed to be weird.

### Performance subspans

One incoming segment may contain more than one performance role. For example:

> “You're early,” Mara said.

The quoted words belong to Mara's performance, while `Mara said.` belongs to the narrator. The current system can identify Mara correctly and still render the entire segment through her voice.

Proscenium should eventually derive performance subspans without destroying the original text or casually rewriting the existing global segmentation system. This is a rendering problem inside a segment, not another speaker-inference problem.

### Character and actor safeguards

Narrative subjects are not automatically characters. Live testing has already produced cases where environmental nouns such as water or thunder were treated as possible actors and allowed to influence nearby dialogue performance.

Before preceding-action evidence is trusted more broadly, performance candidates should be grounded in plausible or already-established literary entities. Solving general linguistic animacy is not required to stop the system from casting the weather.

### Conversation continuity

Sparse dialogue needs stronger continuity without turning alternating lines into false certainty. Paragraph structure, quote continuity, established scene participants, and nearby explicit attribution should provide the strongest deterministic foundation available.

The literary system must remain free to say `unresolved` even when the performance layer makes a conservative choice to keep the conversation listenable.

## Lookahead and preflight analysis

The first offline book-preflight slice now ingests EPUB, PDF, TXT, and Markdown and attaches validated, read-only character priors to a fresh runtime scope. It does not synchronize source positions or assign speakers throughout the document. Discovery coverage, alias identity reconciliation, and live listening validation of prior-assisted actor rejection remain follow-up work; runtime state persistence remains deferred.

The current server largely reasons from text that has already arrived when audio needs to be synthesized. That forces it to solve literature through a narrow moving window, even when the answer may appear a paragraph later.

A future passage-lookahead layer could analyze more text than is immediately spoken. At the beginning of a reading session or chapter, Proscenium could inspect a larger window for quotation conventions, attribution anchors, POV evidence, paragraph relationships, recurring pronoun evidence, and probable scene participants. Once playback begins, a rolling lookahead could continue preparing upcoming text while already-rendered audio is playing.

The intended flow remains:

**lookahead text → observations and evidence → literary state → performance decisions → requested-text synthesis**

Lookahead provides better evidence; it does not convert guesses into facts. The same uncertainty rules should continue to apply.

Whether this can be done through the existing TTS client is still unknown. That question belongs with the document-structure investigation rather than being assumed away.

## Reader integration

PocketBook and the current Android TTS bridge are useful parts of the present system, but they are clients rather than sacred architecture.

If the existing path can provide paragraph structure, stable document position, and enough surrounding text for useful lookahead, there is little reason to replace it merely for architectural purity. If the TTS interface fundamentally reduces an EPUB to isolated fragments, however, a Proscenium-aware reader becomes a serious option rather than a workaround.

A first-party reader would not need to become another Kindle. Its useful responsibilities are much narrower: open and render an EPUB, know the current book/chapter/position, preserve document structure, provide arbitrary context or lookahead to Proscenium, and play or request the resulting audio.

A local web reader may be worth exploring before a native Android application because it could let the existing Proscenium server own the reading context while the browser handles EPUB rendering. The decision should follow the client/protocol investigation, not precede it.

## Probabilistic or model-assisted literary decisions

Model-assisted inference remains part of the direction, but it belongs at genuinely ambiguous decision points rather than in front of every sentence.

A future decision engine may help answer bounded questions such as which established character most likely spoke an unattributed line, whether a pronoun refers to one of the known participants, whether a scene or POV has changed, or whether two references probably identify the same character. It should receive a compact evidence packet from Proscenium and return a decision, candidates, or confidence rather than becoming the database for the book.

Direct evidence outranks learned priors. Style learned from a book or author may influence probability later, but it should never override explicit evidence in the current text. “Unknown” remains an acceptable answer.

## Persistent book memory

Once the state boundaries are mature enough, Proscenium can move beyond process-local reading state.

Likely persistence horizons include scene-level state, chapter or local reading scope, book or edition identity, author/style priors, and reader/performance preferences. These do not all have the same lifetime and should not be dumped into one undifferentiated memory object.

Book identity must be explicit before durable character memory is introduced. Characters, casts, and inferred facts from unrelated books must never leak into one another. A future book container may own multiple scene or POV scopes and reconcile identities deliberately across them.

SQLite remains a plausible first storage layer, but the storage engine is not the difficult decision. The important work is deciding what owns each piece of state, how confident it is, how long it should survive, and what can safely be revised later.

## Performance quality

Correct literary reasoning does not automatically produce good acting. Proscenium's current voice routing is further along than its performance direction, and live listening still exposes mechanical pacing and delivery.

Once the structural and literary pipeline is reliable enough, performance work can expand into dialogue/narration subspan routing, better pauses, more natural conversational rhythm, voice-specific delivery, and cautious expressive or prosodic direction where the synthesis stack permits it.

This should remain its own axis of development. A passage can be understood perfectly and still sound robotic, while a convincing performance can still be based on a wrong literary guess. Proscenium needs to be able to improve one without confusing it with the other.

## Durable runtime behavior

Persistence will eventually force several existing runtime shortcuts to become explicit design decisions. Current state is keyed by client IP, state changes are not transactional, clients can share state unintentionally, and there is no overall client/cast eviction or concurrency coordination.

Those behaviors are acceptable constraints for the current experimental server, but they are not the desired durable architecture. Reading-session identity, book identity, failure recovery, concurrency, reset semantics, and state migration will need explicit contracts before persistence becomes a user-facing feature.

## Distribution

The current development environment works for its author but is not yet a general-user product. Future distribution work includes a validated clean installation, dependency and model revision pinning, client setup, configuration that is not tied to the development machine, listening examples, and eventually packaging that does not require somebody to reconstruct the author's Python environment.

Any installer or integrated binary distribution will also require another licensing and dependency review. The current public repository is a source project and development record, not a promise that a packaged release is ready.

## Direction, not a checklist

This roadmap describes the current order of architectural pressure, not a queue that should be implemented mechanically. New listening evidence can and should change the order.

The near-term rule is simpler than the whole roadmap: preserve more of what the book already tells us, represent uncertainty honestly, keep literary understanding separate from performance convenience, and only add heavier inference when the remaining problem is actually inference.
