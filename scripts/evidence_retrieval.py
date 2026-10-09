"""Read-only, bounded source retrieval from schema-2 BookPreflight artifacts.

Names are lexical spellings (case insensitive), not reconciled identities.
Scores select passages, never establish characters, relationships, or POV.
"""
import re


DESCRIPTION_WORDS = re.compile(r"\b(?:was|were|had|wore|wearing|looked|man|woman|girl|boy)\b", re.I)
NAMING_WORDS = re.compile(r"\b(?:called|named|name|names|nickname)\b", re.I)
FIRST_PERSON = {"i", "me", "my", "mine", "myself", "we", "us", "our", "ours", "ourselves"}


def retrieve_evidence(artifact, kind, surfaces=(), *, max_passages=8,
                      context_blocks=2, block_chars=1200, max_observations=64):
    """Return a detached JSON-ready evidence packet; do not mutate the artifact.

    candidate: one spelling, favor unquoted mentions and descriptive vocabulary.
    relationship: two or more spellings, require all within the context window;
        favor same-block co-occurrence and literal naming vocabulary. No
        identity expansion or conflict filtering occurs. A literal trailing-s
        plural spelling is also matched and labeled, without equating identities.
    narrator: optional spellings, favor nearby unquoted first-person tokens.
        Without spellings, anchor on unquoted first-person observations.

    Rank descending by the reported integer score, then ascending block index.
    One passage per anchor block; overlapping windows are intentionally retained.
    Each block slice centers on its first matching name (or first-person token),
    otherwise starts at zero. Only wholly visible observations are referenced.
    Bounds limit output, not the scan of the artifact. No source is synthesized.
    """
    if kind not in ("candidate", "relationship", "narrator"):
        raise ValueError("Unknown retrieval kind")
    if isinstance(surfaces, str):
        surfaces = (surfaces,)
    if not isinstance(surfaces, (tuple, list)) or any(
            not isinstance(s, str) or not s.strip() for s in surfaces):
        raise ValueError("Surfaces must be nonempty lexical spellings")
    names = tuple(sorted({" ".join(s.split()).casefold() for s in surfaces}))
    if (kind == "candidate" and len(names) != 1 or
            kind == "relationship" and not 2 <= len(names) <= 4 or
            kind == "narrator" and len(names) > 4):
        raise ValueError("Candidate requires one spelling; relationship two to four; narrator zero to four")
    for value, minimum, maximum in ((max_passages, 1, 16), (context_blocks, 0, 3),
                                    (block_chars, 32, 2400), (max_observations, 1, 128)):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError("Retrieval bounds must be integers within supported limits")

    by_block = [[] for _ in artifact.blocks]
    hits = [[] for _ in artifact.blocks]
    first_person = [[] for _ in artifact.blocks]
    matches = {}
    for o in artifact.observations:
        index = o.source.block
        by_block[index].append(o)
        if o.kind == "name-mention":
            spelling = o.normalized_form.casefold()
            matched = [{"surface": name, "rule": "exact-spelling" if spelling == name else "literal-trailing-s",
                        "observed_surface": o.normalized_form, "observation_reference": o.observation_id}
                       for name in names if spelling == name or kind == "relationship" and spelling == name + "s"]
            if matched:
                hits[index].append(o)
                matches[o.observation_id] = matched
        if (o.kind == "pronoun-mention" and not o.in_dialogue and
                o.source.excerpt.casefold() in FIRST_PERSON):
            first_person[index].append(o)

    ranked = []
    for index, block in enumerate(artifact.blocks):
        if not (hits[index] if names else first_person[index]):
            continue
        low, high = max(0, index - context_blocks), min(len(by_block), index + context_blocks + 1)
        window_names = {m["surface"] for group in hits[low:high] for o in group for m in matches[o.observation_id]}
        if kind == "relationship" and not set(names) <= window_names:
            continue
        components = {}
        if kind == "candidate":
            components["unquoted-name-mentions (cap 2, weight 4)"] = 4 * min(2, sum(not o.in_dialogue for o in hits[index]))
            components["literal-description-words (cap 3)"] = min(3, len(DESCRIPTION_WORDS.findall(block.text)))
        elif kind == "relationship":
            same = set(names) <= {m["surface"] for o in hits[index] for m in matches[o.observation_id]}
            components["all-spellings-in-anchor-block (weight 8)"] = 8 * int(same)
            components["literal-naming-words (cap 2, weight 2)"] = 2 * min(2, len(NAMING_WORDS.findall(block.text)))
        else:
            components["unquoted-first-person-in-window (cap 4)"] = min(4, sum(len(group) for group in first_person[low:high]))
            components["unquoted-first-person-in-anchor (cap 2)"] = min(2, len(first_person[index]))
        score = sum(components.values())
        ranked.append((score, index, low, high, components))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    passages = []
    for score, index, low, high, components in ranked[:max_passages]:
        sources = []
        for number in range(low, high):
            block = artifact.blocks[number]
            focus = hits[number] or first_person[number]
            center = (focus[0].source.start + focus[0].source.end) // 2 if focus else 0
            start = min(max(0, center - block_chars // 2), max(0, len(block.text) - block_chars))
            end = min(len(block.text), start + block_chars)
            visible = [o for o in by_block[number] if start <= o.source.start and o.source.end <= end]
            # Keep query anchors available even when the reference cap is small;
            # restore source order after choosing anchor references first.
            first_person_ids = {o.observation_id for o in first_person[number]}
            selected_ids = {o.observation_id for o in sorted(visible, key=lambda o: (
                o.observation_id not in matches, o.observation_id not in first_person_ids,
                int(o.observation_id[1:])))[:max_observations]}
            selected = [o for o in visible if o.observation_id in selected_ids]
            sources.append({"block": number, "locator": block.locator, "start": start, "end": end,
                            "excerpt": block.text[start:end], "text_clipped": start > 0 or end < len(block.text),
                            "observation_references": [o.observation_id for o in selected],
                            "surface_matches": [m for o in selected for m in matches.get(o.observation_id, ())],
                            "total_visible_observations": len(visible),
                            "observation_references_clipped": len(visible) > max_observations})
        passages.append({"anchor_block": index, "score": score, "selection_rationale": components,
                         "sources": sources})
    return {"document_sha256": artifact.document.sha256,
            "query": {"kind": kind, "surfaces": list(names)},
            "limits": {"max_passages": max_passages, "context_blocks": context_blocks,
                       "block_chars": block_chars, "max_observations_per_block": max_observations},
            "ordering": "descending selection score, ascending anchor block; sources in block order",
            "observation_selection": "query-name matches, unquoted first-person, then source order; returned references in source order",
            "total_matching_anchors": len(ranked), "selection_clipped": len(ranked) > max_passages,
            "empty_reason": "no-matching-source-window" if not passages else None,
            "passages": passages}
