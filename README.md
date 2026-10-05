# The Margin Proscenium

I read a lot of ebooks by listening to them. Ordinary text-to-speech is remarkably useful for that, but it doesn't really *read a story*. It speaks text. The narrator, the characters, the dialogue, the little "she said" after a line: to a normal TTS system, they're all fundamentally the same thing.

A professionally produced audiobook solves that beautifully, when one exists. But that leaves a whole lot of perfectly good ebooks sitting between two worlds: they can be read aloud by a computer, but they haven't really been given a performance.

**The Margin Proscenium asks what we can do with the ebook itself.**

It's an experimental **Red Work Atelier** reading system that tries to turn ordinary ebook text into something closer to a performance. Instead of treating a book as a stream of sentences to pronounce, Proscenium tries to understand enough of what it's reading to make useful performance decisions. Who is speaking? Is this dialogue or narration? Have we met this character before? Does this voice belong to them? Are these two people talking back and forth? Is this sentence part of the same quotation or paragraph as the one before it?

The ebook is still the source. Proscenium is the machinery around the performance.

## Where it is now

This is very much a work in progress. The current Proscenium development path can recognize narration and many forms of dialogue, identify explicit speaker cues, follow some conversations, assign recurring voices to characters, and preserve uncertainty when it genuinely doesn't know who is speaking.

That last part matters. One of the project's guiding ideas is that **guessing for the performance and deciding what the book actually says are not the same thing**. Proscenium can make a tentative choice about which voice would make a passage sound better without turning that guess into a fact about the story.

It still gets things wrong, because real books are wonderfully inconvenient. Speakers aren't always named. Dialogue can continue across sentences and paragraphs. An attribution may come after the words it identifies. A character can perform an action without speaking. First-person narration means that "I" can be both the narrator of the prose and a character speaking aloud. Even whitespace and paragraph boundaries contain information that a human reader absorbs without thinking about it.

Those are exactly the problems Proscenium is being built to explore. The current v0.16 work is focused on giving that literary reasoning an explicit state of its own: what the system observed, what it inferred, what remains uncertain, and what performance decision it ultimately made.

Natural performance is a separate mountain still waiting to be climbed. The voices can currently sound mechanical, and pacing and delivery remain fairly simple. For now, getting the *structure* of the reading right comes before teaching the actors to chew the scenery properly.

## Two ways to listen

### Proscenium

This is the experimental multi-voice path and the reason this repository exists. Proscenium tries to distinguish narration from dialogue, identify or infer speakers, maintain a cast, and route different parts of the text to appropriate voices.

The long-term idea goes beyond simply swapping voices. A reading system should be able to build an understanding of a book as it progresses, use surrounding literary context, look ahead when useful, remember what it has learned, and turn that understanding into a better performance.

We're not there yet, but that's the direction.

### Single-voice narration

There is also a deliberately simpler v0.11-derived path. It uses one narrator voice and avoids Proscenium's experimental character casting and speaker inference. It exists because sometimes I just want the computer to read the damn book.

The existing development launcher lets me choose between the two, so the experimental work doesn't have to make the reliable single-voice reader increasingly complicated just because Proscenium is off learning how dialogue works.

## Can I actually use this?

Sort of.

The development setup works. I use it. But this is **not yet a polished download-and-listen application**. There is currently no installer or packaged Proscenium release, and installation on a fresh computer has not been validated. The present setup expects a Python development environment and some manual configuration before an ebook reader can start talking to it.

If that sounds like a perfectly reasonable way to spend an evening, [DEVELOPMENT.md](DEVELOPMENT.md) documents the current environment and launch commands. If it does not, you probably want to watch the project for now rather than install it.

Making the system easier for other people to use is part of the eventual plan, along with better reader integration, persistent book and character state, listening examples, and a much less developer-shaped installation experience.

## Why "Proscenium"?

A proscenium is the architectural frame around a theatrical stage: the opening through which the audience sees the performance. That felt right.

An ebook gives us the words. Kokoro gives us voices. Proscenium is becoming the machinery between them: part parser, part casting desk, part stage manager, trying to work out who is on stage and what should happen when the curtain opens.

The goal isn't to magically turn every EPUB into a professionally produced audiobook. It's to make an ordinary ebook feel less like **text being spoken** and more like **a story being read**.

## Local speech generation

Speech is generated on the local computer using Kokoro. The current narration code does not send book text to a hosted speech-generation service, although software, models, and voice files may still need to be downloaded. "Local" therefore shouldn't be interpreted as a blanket privacy guarantee.

The current setup is designed for a trusted local environment, not as a public internet-facing service.

## For the technically curious

The repository also contains the less theatrical machinery underneath all of this:

- [Development](DEVELOPMENT.md) covers the current environment, launch commands, tests, and known setup limitations.
- [Architecture](ARCHITECTURE.md) describes how the reading pipeline currently works and the boundaries between literary reasoning and performance.
- [Roadmap](ROADMAP.md) tracks where the project is headed.
- [Agent guidance](AGENTS.md) provides working context for Codex sessions.
- [Publication and distribution](PUBLICATION.md) documents repository boundaries and third-party licensing considerations.

Older browser-extension files are also preserved in the repository, although they do not work unchanged with the current Proscenium setup.

## Red Work Atelier

**The Margin Proscenium is a Red Work Atelier project.**

Red Work Atelier is where I put projects that tend to begin with "surely this could work better" and then get somewhat out of hand.

*From complexity to coherence, craft before chrome.*

Copyright (c) 2026 Red Work Atelier

Original project software is licensed under the [Mozilla Public License 2.0](LICENSE); see [NOTICE](NOTICE). Third-party dependencies and downloaded models retain their own licenses. See the [distribution notes](PUBLICATION.md#dependencies-and-distribution) for the distinction and for considerations around future packaged releases.