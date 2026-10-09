"""Offline document observations and immutable book priors; no synthesis or runtime memory.

Run: python -m scripts.book_preflight book.epub -o book.preflight.json
Discovery is deliberately conservative, English-oriented, and non-exhaustive.
"""
import argparse
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import posixpath
import re
from typing import Literal, get_origin
from urllib.parse import unquote
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


NAME = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’-]+"
FULL_NAME = rf"{NAME}(?:\s+{NAME}){{0,2}}"
NON_NAMES = set("The A An And But Then There He She They I We You It His Her Their Nothing Nobody Someone Something Chapter Mr Mrs Miss Doctor Captain".split())
NON_NAMES.update("After Although Another Any Anyone Anything As At Before Behind Below Beneath Beside Between Beyond Both By Come Despite Either Every Everyone Everything Finally For From How If In Inside Later Meanwhile Neither No Not Now Once Or Outside Over Perhaps Please Presently So Still That Their This Through Till To Toward Under Until Upon Well What When Where Which While Who Why With Yes Your Tell".split())
NON_NAME_KEYS = {name.casefold() for name in NON_NAMES}
SPEECH = r"(?:said|says|asked|asks|replied|answered|whispered|shouted|murmured|exclaimed|responded|retorted)"
SPEECH_BEFORE = re.compile(rf"\b(?P<name>{FULL_NAME})\s+{SPEECH}\b(?!\s+(?:nothing|no\s+one|not)\b)")
SPEECH_AFTER = re.compile(rf"\b{SPEECH}\s+(?P<name>{FULL_NAME})\b")
ACTION = re.compile(rf"\b(?P<name>{FULL_NAME})\s+(?:smiled|frowned|nodded|waved|crouched|knelt|shrugged|bowed)\b")
ALIAS = re.compile(rf"\b(?P<name>{FULL_NAME}),?\s+(?:also known as|called herself|called himself)\s+(?P<alias>{FULL_NAME})\b")
REFLEXIVE = re.compile(rf"\b(?P<name>{FULL_NAME})\s+(?:introduced|dressed|washed|blamed|called)\s+(?P<pronoun>herself|himself|themself|themselves)\b")
NAME_TOKEN = re.compile(rf"\b{NAME}\b")
NAME_RUN = re.compile(rf"\b{FULL_NAME}\b")
PRONOUN_TOKEN = re.compile(r"\b(?:I|me|my|mine|myself|we|us|our|ours|ourselves|he|him|his|himself|she|her|hers|herself|they|them|their|theirs|themself|themselves|you|your|yours|yourself)\b", re.I)
REFERENCE_SPEECH = re.compile(rf"\b(?:(?:I|he|she|they|we|you)\s+{SPEECH}|{SPEECH}\s+(?:I|he|she|they|we|you))\b", re.I)


def clean_name(raw):
    """Normalize spelling only, never infer that a mention is a person."""
    parts = raw.split()
    while parts and parts[0].casefold() in NON_NAME_KEYS:
        parts.pop(0)
    if not parts or any(p.casefold() in NON_NAME_KEYS for p in parts):
        return None
    if len(parts) > 1 and any(re.search(r"['’]s$", p) for p in parts):
        return None  # 'Laurie's English friends' is not the compound name Laurie English.
    value = " ".join(re.sub(r"['’]s$", "", p) for p in parts)
    if any(p.casefold() in NON_NAME_KEYS for p in value.split()):
        return None
    return value if re.fullmatch(FULL_NAME, value) else None


def named_cues(surface):
    """Narrow existing promotion anchors, with source-preserving subject cleanup."""
    for pattern, kind, rule in ((SPEECH_BEFORE, "speech-attribution", "named-speech"),
                                (SPEECH_AFTER, "speech-attribution", "named-speech"),
                                (ACTION, "human-action", "human-action")):
        for match in pattern.finditer(surface):
            raw = match["name"]
            # A possessive subject (e.g. 'said Laurie's eyes') is not a speech anchor.
            if re.search(r"['’]s\b", raw):
                continue
            tokens = list(re.finditer(r"\S+", raw))
            first = 0
            while first < len(tokens) and tokens[first][0].casefold() in NON_NAME_KEYS:
                first += 1
            if tokens[0][0].casefold() in {"tell", "please", "come"}:
                first = len(tokens) - 1  # 'Tell Beth Frank asked' anchors only Frank.
            if first == len(tokens):
                continue
            name = clean_name(raw[tokens[first].start():])
            if name:
                start = match.start("name") + tokens[first].start() if pattern is not SPEECH_AFTER else match.start()
                yield name, start, match.end(), kind, rule


class PriorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def json_arrays(cls, value, info):
        # HTTP JSON arrives as Python lists; normalize containers only, while
        # retaining strict scalar types and immutable internal collections.
        if get_origin(cls.model_fields[info.field_name].annotation) is tuple and isinstance(value, list):
            return tuple(value)
        return value


class DocumentIdentity(PriorModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    filename: str = Field(min_length=1)
    format: Literal["epub", "pdf", "txt", "md"]
    title: str | None = None
    authors: tuple[str, ...] = ()
    identifiers: tuple[str, ...] = ()
    language: str | None = None


class TextBlock(PriorModel):
    locator: str = Field(min_length=1)
    kind: Literal["paragraph", "heading", "page"]
    text: str = Field(min_length=1)


class SourceSpan(PriorModel):
    block: int = Field(ge=0)
    locator: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    excerpt: str = Field(min_length=1)


class LiteraryObservation(PriorModel):
    """Textual facts only. Proximity and quotation do not establish identity."""
    observation_id: str = Field(pattern=r"^o[0-9]+$")
    kind: Literal["name-mention", "pronoun-mention", "dialogue-span", "speech-attribution", "human-action", "alias-cue"]
    basis: Literal["observation"] = "observation"
    rule: Literal["capitalized-surface", "pronoun-token", "quotation-marks", "named-speech", "reference-speech", "human-action", "explicit-alias"]
    source: SourceSpan
    normalized_form: str | None = None
    in_dialogue: bool

    @model_validator(mode="after")
    def check_kind(self):
        kinds = {"capitalized-surface": "name-mention", "pronoun-token": "pronoun-mention",
                 "quotation-marks": "dialogue-span", "named-speech": "speech-attribution",
                 "reference-speech": "speech-attribution", "human-action": "human-action",
                 "explicit-alias": "alias-cue"}
        if self.kind != kinds[self.rule] or (self.kind == "name-mention") != (self.normalized_form is not None):
            raise ValueError("Observation rule, kind and name fields must agree")
        return self


class IdentityCandidate(PriorModel):
    """A lexical hypothesis, never a promoted character or pronoun binding."""
    surface: str = Field(pattern=rf"^{FULL_NAME}$")
    basis: Literal["inference"] = "inference"
    status: Literal["unassessed"] = "unassessed"
    rule: Literal["name-surface-candidate"] = "name-surface-candidate"
    occurrences: tuple[str, ...] = Field(min_length=1)

    @field_validator("surface")
    @classmethod
    def clean_surface(cls, value):
        if clean_name(value) != value:
            raise ValueError("Candidate spelling must be normalized, without discourse starters")
        return value


class PriorEvidence(PriorModel):
    block: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    excerpt: str = Field(min_length=1)
    kind: Literal["speech-attribution", "human-action", "alias", "pronoun"]
    basis: Literal["observation", "inference"]
    rule: Literal["named-speech", "human-action", "explicit-alias", "unique-name-component", "subject-reflexive"]

    @model_validator(mode="after")
    def check_rule(self):
        expected = {
            "named-speech": ("speech-attribution", "observation"),
            "human-action": ("human-action", "observation"),
            "explicit-alias": ("alias", "observation"),
            "unique-name-component": ("alias", "inference"),
            "subject-reflexive": ("pronoun", "inference"),
        }
        if (self.kind, self.basis) != expected[self.rule] or self.end <= self.start:
            raise ValueError("Inconsistent evidence rule or span")
        return self


class NameVariant(PriorModel):
    name: str = Field(pattern=rf"^{FULL_NAME}$")
    evidence: tuple[PriorEvidence, ...] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def clean_variant(cls, value):
        if clean_name(value) != value:
            raise ValueError("Alias spelling must be normalized")
        return value


class PronounEvidence(PriorModel):
    pronoun: Literal["herself", "himself", "themself", "themselves"]
    gender: Literal["female", "male"] | None = None
    evidence: PriorEvidence

    @model_validator(mode="after")
    def check_gender(self):
        if self.gender != {"herself": "female", "himself": "male"}.get(self.pronoun):
            raise ValueError("Gender must reflect the recorded pronoun, never a name guess")
        if self.evidence.kind != "pronoun":
            raise ValueError("Pronoun evidence required")
        return self


class CharacterPrior(PriorModel):
    identity: str = Field(pattern=rf"^{FULL_NAME}$")
    aliases: tuple[NameVariant, ...] = ()
    evidence: tuple[PriorEvidence, ...] = Field(min_length=1)
    pronouns: tuple[PronounEvidence, ...] = ()
    status_basis: Literal["inference"] = "inference"
    confidence: Literal["high", "medium"]

    @field_validator("identity")
    @classmethod
    def clean_identity(cls, value):
        if clean_name(value) != value:
            raise ValueError("Promoted identity must be normalized")
        return value

    @model_validator(mode="after")
    def check_character(self):
        if any(e.kind not in ("speech-attribution", "human-action") for e in self.evidence):
            raise ValueError("Character status requires speech or human action evidence")
        speech = any(e.kind == "speech-attribution" for e in self.evidence)
        if not speech and len({(e.block, e.start) for e in self.evidence}) < 2:
            raise ValueError("Action-only candidates require repeated support")
        if self.confidence != ("high" if speech else "medium"):
            raise ValueError("Confidence must reflect discovery support")
        names = [a.name for a in self.aliases]
        if len(set(names)) != len(names) or self.identity in names:
            raise ValueError("Duplicate aliases")
        return self


class BookPreflight(PriorModel):
    schema_version: Literal[2]
    discovery_version: Literal["deterministic-en-2"]
    document: DocumentIdentity
    blocks: tuple[TextBlock, ...] = Field(min_length=1)
    observations: tuple[LiteraryObservation, ...]
    candidates: tuple[IdentityCandidate, ...]
    characters: tuple[CharacterPrior, ...] = ()
    warnings: tuple[str, ...] = ()

    @field_validator("schema_version", mode="before")
    @classmethod
    def check_version(cls, value):
        if type(value) is not int:
            raise ValueError("Schema version must be an integer")
        return value

    @model_validator(mode="after")
    def check_evidence(self):
        names = [c.identity for c in self.characters]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate character identities")
        if len({b.locator for b in self.blocks}) != len(self.blocks):
            raise ValueError("Duplicate block locators")
        masks = quote_surfaces(self.blocks)
        observed = {}
        previous = None
        for index, observation in enumerate(self.observations):
            source = observation.source
            if observation.observation_id != f"o{index}":
                raise ValueError("Observation IDs must be unique and ordered")
            if (source.block >= len(self.blocks) or source.start >= source.end or
                    source.end > len(self.blocks[source.block].text) or
                    source.locator != self.blocks[source.block].locator or
                    self.blocks[source.block].text[source.start:source.end] != source.excerpt):
                raise ValueError("Observation span/locator must match extracted source")
            key = (source.block, source.start, source.end, observation.kind)
            if previous is not None and key <= previous:
                raise ValueError("Observations must be distinct and source-ordered")
            previous = key
            _, mask = masks[source.block]
            if observation.in_dialogue != all(mask[source.start:source.end]):
                raise ValueError("Quotation context must match source punctuation")
            if observation.kind in ("speech-attribution", "human-action", "alias-cue") and any(mask[source.start:source.end]):
                raise ValueError("Narrative cues cannot come from words inside dialogue")
            text = source.excerpt
            if observation.kind == "name-mention":
                if (not re.fullmatch(FULL_NAME, text) or clean_name(text) != observation.normalized_form or
                        any(p.casefold() in NON_NAME_KEYS for p in text.split())):
                    raise ValueError("Name observation must support a clean lexical form")
            elif observation.kind == "pronoun-mention" and not PRONOUN_TOKEN.fullmatch(text):
                raise ValueError("Pronoun observation must be a source pronoun")
            elif observation.rule == "reference-speech" and not REFERENCE_SPEECH.fullmatch(text):
                raise ValueError("Reference speech observation requires a textual tag")
            elif observation.rule in ("named-speech", "human-action"):
                if not any(start == 0 and end == len(text) and rule == observation.rule
                           for _, start, end, _, rule in named_cues(text)):
                    raise ValueError("Named cue must match its source anchor")
            elif observation.kind == "alias-cue" and not ALIAS.fullmatch(text):
                raise ValueError("Alias cue must match its source construction")
            elif observation.kind == "dialogue-span" and not observation.in_dialogue:
                raise ValueError("Dialogue observation must be within quotation marks")
            if observation.kind == "dialogue-span" and ((source.start and mask[source.start - 1]) or
                    (source.end < len(mask) and mask[source.end])):
                raise ValueError("Dialogue observations must retain the complete block-local quoted span")
            observed[observation.observation_id] = observation
        surfaces = [c.surface for c in self.candidates]
        if len(set(surfaces)) != len(surfaces):
            raise ValueError("Duplicate candidate surfaces")
        referenced = set()
        for candidate in self.candidates:
            if len(set(candidate.occurrences)) != len(candidate.occurrences):
                raise ValueError("Duplicate candidate occurrence references")
            positions = []
            for ref in candidate.occurrences:
                occurrence = observed.get(ref)
                if not occurrence or occurrence.kind != "name-mention" or occurrence.normalized_form != candidate.surface:
                    raise ValueError("Candidate references must support its lexical surface")
                positions.append(int(ref[1:]))
                referenced.add(ref)
            if positions != sorted(positions):
                raise ValueError("Candidate occurrences must be source-ordered")
        if referenced != {o.observation_id for o in self.observations if o.kind == "name-mention"}:
            raise ValueError("Every name observation must remain available as candidate evidence")
        anchors = {(o.kind, o.source.block, o.source.start, o.source.end) for o in self.observations}
        for character in self.characters:
            if character.identity not in surfaces:
                raise ValueError("Promoted characters must have candidate observations")
            evidence = list(character.evidence)
            evidence.extend(e for a in character.aliases for e in a.evidence)
            evidence.extend(p.evidence for p in character.pronouns)
            for e in evidence:
                if e.block >= len(self.blocks) or e.end > len(self.blocks[e.block].text) or self.blocks[e.block].text[e.start:e.end] != e.excerpt:
                    raise ValueError("Evidence must match extracted source text")
            for e in character.evidence:
                patterns = (SPEECH_BEFORE, SPEECH_AFTER) if e.kind == "speech-attribution" else (ACTION,)
                if not any((match := pattern.fullmatch(e.excerpt)) and
                           clean_name(match["name"]) == character.identity for pattern in patterns):
                    raise ValueError("Character evidence must support its named identity")
                if (e.kind, e.block, e.start, e.end) not in anchors:
                    raise ValueError("Promotion anchors must be recorded observations")
            for alias in character.aliases:
                for e in alias.evidence:
                    if e.rule == "explicit-alias":
                        match = ALIAS.fullmatch(e.excerpt)
                        if not match or " ".join(match["name"].split()) != character.identity or " ".join(match["alias"].split()) != alias.name:
                            raise ValueError("Alias evidence must name the identity and variant")
                    elif e.rule == "unique-name-component":
                        if alias.name not in character.identity.split() or sum(alias.name in n.split() for n in names) != 1:
                            raise ValueError("Name-component alias must be unambiguous")
                        if not any((e.block, e.start, e.end) == (source.block, source.start, source.end) for source in character.evidence):
                            raise ValueError("Name-component aliases require character evidence")
                    else:
                        raise ValueError("Alias evidence rule required")
            for pronoun in character.pronouns:
                match = REFLEXIVE.fullmatch(pronoun.evidence.excerpt)
                if not match or " ".join(match["name"].split()) != character.identity or match["pronoun"] != pronoun.pronoun:
                    raise ValueError("Pronoun evidence must support this identity and pronoun")
        return self

    def find_character(self, name: str):
        """Ambiguous aliases never validate a runtime candidate."""
        direct = next((c for c in self.characters if c.identity == name), None)
        if direct:
            return direct
        matches = [c for c in self.characters if any(a.name == name for a in c.aliases)]
        return matches[0] if len(matches) == 1 else None

    def to_json(self):
        return self.model_dump_json(indent=2)

    def candidate_packet(self, surface: str, *, max_occurrences=4, max_nearby=8, context_chars=160):
        """Build bounded source evidence for later reasoning; perform no inference.

        Nearby pronouns and dialogue are unbound observations, not candidate traits.
        Sampling the first occurrences is explicit; full evidence stays in the artifact.
        """
        for value, maximum in ((max_occurrences, 8), (max_nearby, 16), (context_chars, 320)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Packet bounds must be positive integers within supported limits")
        candidate = next((c for c in self.candidates if c.surface == surface), None)
        if candidate is None:
            raise ValueError("Unknown candidate surface")
        by_id = {o.observation_id: o for o in self.observations}
        selected = []
        for ref in candidate.occurrences[:max_occurrences]:
            observation = by_id[ref]
            source = observation.source
            block = self.blocks[source.block]
            start, end = max(0, source.start - context_chars), min(len(block.text), source.end + context_chars)
            nearby = [o for o in self.observations if o.source.block == source.block
                      and o.observation_id != ref and start <= o.source.start and o.source.end <= end]
            # Keep useful non-name cues ahead of competing lexical mentions.
            nearby.sort(key=lambda o: (o.kind == "name-mention", abs(o.source.start - source.start), int(o.observation_id[1:])))
            selected.append({"occurrence": observation.model_dump(mode="json"),
                "context": SourceSpan(block=source.block, locator=block.locator, start=start, end=end,
                                      excerpt=block.text[start:end]).model_dump(mode="json"),
                "nearby_observations": [o.model_dump(mode="json") for o in nearby[:max_nearby]]})
        return {"schema_version": self.schema_version, "discovery_version": self.discovery_version,
                "document": self.document.model_dump(mode="json"), "surface": surface,
                "basis": candidate.basis, "status": candidate.status,
                "sampling": "first-occurrences; nearby cues wholly within context",
                "total_occurrences": len(candidate.occurrences), "occurrences": selected}

    @classmethod
    def from_json(cls, value: str):
        envelope = json.loads(value)
        if not isinstance(envelope, dict) or type(envelope.get("schema_version")) is not int or envelope["schema_version"] != 2:
            raise ValueError("Unsupported preflight schema version; regenerate artifacts from source for schema 2")
        if set(envelope) != set(cls.model_fields):
            raise ValueError("A complete versioned preflight envelope is required")
        return cls.model_validate_json(value)


class ReadableHTML(HTMLParser):
    """Preserve block breaks and headings; omit navigation, scripts and styles."""
    boundaries = {"p", "div", "section", "article", "li", "blockquote", "br", "h1", "h2", "h3", "h4", "h5", "h6"}
    ignored = {"script", "style", "nav", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.buffer = []
        self.skip = []
        self.kind = "paragraph"

    def flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buffer)).strip()
        if text:
            self.parts.append((self.kind, text))
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        if self.skip or tag in self.ignored:
            if tag not in {"br", "img", "meta", "link", "hr", "input"}:
                self.skip.append(tag)
            return
        if tag in self.boundaries:
            self.flush()
            self.kind = "heading" if re.fullmatch(r"h[1-6]", tag) else "paragraph"

    def handle_endtag(self, tag):
        if self.skip:
            if tag in self.skip:
                self.skip = self.skip[:self.skip.index(tag)]
            return
        if tag in self.boundaries:
            self.flush()
            self.kind = "paragraph"

    def handle_data(self, data):
        if not self.skip:
            self.buffer.append(data)


def _paragraphs(text, prefix, markdown=False):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if markdown:
        # Basic prose Markdown only: code is not literary source evidence.
        text = re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1\s*$", "", text)
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"`[^`\n]+`", "", text)
        text = re.sub(r"(?m)^\s*>\s?", "", text)
        text = re.sub(r"(?m)^(#{1,6} .+)\n(?=\S)", r"\1\n\n", text)
    blocks = []
    for chunk in re.split(r"\n\s*\n", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        heading = markdown and bool(re.match(r"^#{1,6}\s", chunk))
        if heading:
            chunk = re.sub(r"^#{1,6}\s+", "", chunk)
        if markdown:
            chunk = chunk.replace("**", "").replace("__", "").replace("`", "")
            chunk = re.sub(r"(?<!\w)([*_])(?=\S)(.+?)(?<=\S)\1(?!\w)", r"\2", chunk)
        blocks.append(TextBlock(locator=f"{prefix}:{len(blocks) + 1}", kind="heading" if heading else "paragraph", text=chunk))
    return blocks


def ingest_document(path: str | Path):
    path = Path(path)
    fmt = path.suffix.lower().removeprefix(".")
    if fmt not in ("epub", "pdf", "txt", "md"):
        raise ValueError("Supported document formats: EPUB, PDF, TXT, .md")
    raw = path.read_bytes()
    metadata = dict(sha256=sha256(raw).hexdigest(), filename=path.name, format=fmt)
    warnings = []
    if fmt in ("txt", "md"):
        text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
        blocks = _paragraphs(text, fmt, markdown=fmt == "md")
        if fmt == "md" and blocks and blocks[0].kind == "heading":
            metadata["title"] = blocks[0].text
    elif fmt == "epub":
        blocks = []
        with ZipFile(BytesIO(raw)) as archive:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = container.find(".//{*}rootfile")
            if rootfile is None:
                raise ValueError("EPUB container has no package rootfile")
            package_path = rootfile.attrib["full-path"]
            package = ET.fromstring(archive.read(package_path))
            meta = package.find("{*}metadata")
            if meta is not None:
                for field, tag in (("title", "title"), ("language", "language")):
                    item = meta.find("{*}" + tag)
                    if item is not None and item.text:
                        metadata[field] = item.text.strip()
                for field, tag in (("authors", "creator"), ("identifiers", "identifier")):
                    metadata[field] = tuple(i.text.strip() for i in meta.findall("{*}" + tag) if i.text)
            manifest = {i.attrib["id"]: i for i in package.findall("{*}manifest/{*}item")}
            for ref in package.findall("{*}spine/{*}itemref"):
                item = manifest[ref.attrib["idref"]]
                if item.attrib.get("media-type") not in ("application/xhtml+xml", "text/html"):
                    warnings.append(f"Skipped non-text spine item: {item.attrib['id']}")
                    continue
                member = posixpath.normpath(posixpath.join(posixpath.dirname(package_path), unquote(item.attrib["href"].split("#")[0])))
                if member.startswith(("../", "/")):
                    raise ValueError("EPUB spine path escapes archive root")
                # XML honors XHTML encoding declarations; HTML fallback is UTF-8.
                source = archive.read(member)
                try:
                    root = ET.fromstring(source)
                    for element in root.iter():
                        element.tag = element.tag.rsplit("}", 1)[-1]
                    source = ET.tostring(root, encoding="unicode")
                except ET.ParseError:
                    source = source.decode("utf-8-sig")
                parser = ReadableHTML()
                parser.feed(source)
                parser.flush()
                blocks.extend(TextBlock(locator=f"{member}:{index}", kind=kind, text=text)
                              for index, (kind, text) in enumerate(parser.parts, 1))
    else:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF ingestion requires pypdf; install requirements-preflight.txt") from exc
        reader = PdfReader(BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")
        meta = reader.metadata
        if meta:
            metadata["title"] = str(meta.title) if meta.title else None
            metadata["authors"] = (str(meta.author),) if meta.author else ()
        blocks = []
        for number, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            if text:
                blocks.append(TextBlock(locator=f"page:{number}", kind="page", text=text))
            else:
                warnings.append(f"Page {number} has no extractable text; OCR is not provided")
        warnings.append("PDF page order is preserved; paragraph and column order may be unreliable")
    blocks = exclude_gutenberg_boilerplate(blocks, warnings)
    if not blocks:
        raise ValueError("Document has no readable text (scanned PDFs require external OCR)")
    return DocumentIdentity(**metadata), tuple(blocks), tuple(warnings)


def exclude_gutenberg_boilerplate(blocks, warnings):
    """Only explicit full-block Gutenberg separators define removal boundaries.

    Keep original coarse locators, work text and independently extracted metadata.
    A Gutenberg mention in prose or an unrecognized publisher is left alone.
    """
    marker = re.compile(r"^\*{3}\s*(START|END) OF (?:THE|THIS) PROJECT GUTENBERG (?:EBOOK|ETEXT)\b.*?\*{3}$", re.I)
    starts, ends = [], []
    for index, block in enumerate(blocks):
        match = marker.fullmatch(block.text.strip())
        if match:
            (starts if match[1].upper() == "START" else ends).append(index)
    if len(starts) > 1 or len(ends) > 1 or (starts and ends and starts[0] >= ends[0]):
        warnings.append("Ambiguous Project Gutenberg separators; boilerplate retained")
        return blocks
    first = starts[0] + 1 if starts else 0
    last = ends[0] if ends else len(blocks)
    # These Gutenberg fixtures place a cover-art license immediately before END,
    # outside the footer. Remove only this exact, identifiable two-block tail.
    if (ends and last - first >= 2 and
            re.fullmatch(r"Transcriber['’]s Notes", blocks[last - 2].text, re.I) and
            blocks[last - 1].text == "New original cover art included with this eBook is granted to the public domain."):
        last -= 2
    if starts or ends:
        warnings.append(f"Excluded {first + len(blocks) - last} Project Gutenberg boilerplate blocks using explicit separators")
    return blocks[first:last]


def _quote_surface(text, opened=False):
    # Replace quotation content with spaces so offsets remain source-relative.
    result, mask = [], []
    for char in text:
        if char == "“":
            opened = True
        elif char == "”":
            opened = False
        elif char == '"':
            opened = not opened
        quoted = opened or char in '“”"'
        result.append(" " if quoted else char)
        mask.append(quoted)
    return "".join(result), mask, opened


def _outside_quotes(text, opened=False):
    surface, _, opened = _quote_surface(text, opened)
    return surface, opened


def quote_surfaces(blocks):
    result, opened = [], False
    for block in blocks:
        surface, mask, opened = _quote_surface(block.text, opened)
        result.append((surface, mask))
    return result


def collect_observations(blocks):
    """Lexical/source observations only; name shape does not prove personhood."""
    records = []
    for index, (block, (outside, mask)) in enumerate(zip(blocks, quote_surfaces(blocks))):
        if block.kind == "heading":
            continue

        def add(start, end, kind, rule, normalized=None):
            records.append(dict(kind=kind, rule=rule, normalized_form=normalized,
                source=SourceSpan(block=index, locator=block.locator, start=start, end=end,
                                  excerpt=block.text[start:end]), in_dialogue=all(mask[start:end])))

        # Preserve individual names even in a capitalized phrase we cannot parse.
        # Only retain a compound surface when no discourse/grammatical token was swallowed.
        matches = [(m.start(), m.end(), m[0]) for m in NAME_TOKEN.finditer(block.text)]
        matches.extend((m.start(), m.end(), m[0]) for m in NAME_RUN.finditer(block.text)
                       if len(m[0].split()) > 1 and not any(p.casefold() in NON_NAME_KEYS for p in m[0].split()))
        for start, end, raw in matches:
            name = clean_name(raw)
            if name and not any(p.casefold() in NON_NAME_KEYS for p in raw.split()):
                add(start, end, "name-mention", "capitalized-surface", name)
        for match in PRONOUN_TOKEN.finditer(block.text):
            add(match.start(), match.end(), "pronoun-mention", "pronoun-token")
        for match in re.finditer(r"1+", "".join("1" if quoted else "0" for quoted in mask)):
            add(match.start(), match.end(), "dialogue-span", "quotation-marks")
        for _, start, end, kind, rule in named_cues(outside):
            if not re.search(r'[“”"]', block.text[start:end]):
                add(start, end, kind, rule)
        for match in REFERENCE_SPEECH.finditer(outside):
            if REFERENCE_SPEECH.fullmatch(block.text[match.start():match.end()]):
                add(match.start(), match.end(), "speech-attribution", "reference-speech")
        for match in ALIAS.finditer(outside):
            if ALIAS.fullmatch(block.text[match.start():match.end()]):
                add(match.start(), match.end(), "alias-cue", "explicit-alias")
    records.sort(key=lambda o: (o["source"].block, o["source"].start, o["source"].end, o["kind"]))
    observations = tuple(LiteraryObservation(observation_id=f"o{index}", **record) for index, record in enumerate(records))
    occurrences = {}
    for observation in observations:
        if observation.kind == "name-mention":
            occurrences.setdefault(observation.normalized_form, []).append(observation.observation_id)
    candidates = tuple(IdentityCandidate(surface=surface, occurrences=tuple(refs)) for surface, refs in sorted(occurrences.items()))
    return observations, candidates


def discover_characters(blocks):
    support, surfaces = {}, []
    opened = False
    for index, block in enumerate(blocks):
        surface, opened = _outside_quotes(block.text, opened)
        surfaces.append(surface)
        if block.kind == "heading":
            continue
        for name, start, end, kind, rule in named_cues(surface):
            if re.search(r'[“”"]', block.text[start:end]):
                continue
            e = PriorEvidence(block=index, start=start, end=end,
                              excerpt=block.text[start:end], kind=kind, basis="observation", rule=rule)
            support.setdefault(name, []).append(e)
    known = {name: evidence for name, evidence in support.items()
             if any(e.kind == "speech-attribution" for e in evidence)
             or len({(e.block, e.start) for e in evidence}) >= 2}
    aliases = {name: [] for name in known}
    pronouns = {name: [] for name in known}
    for index, surface in enumerate(surfaces):
        for match in ALIAS.finditer(surface):
            name, alias = " ".join(match["name"].split()), " ".join(match["alias"].split())
            if name in known and alias != name and clean_name(alias) == alias and ALIAS.fullmatch(blocks[index].text[match.start():match.end()]):
                e = PriorEvidence(block=index, start=match.start(), end=match.end(),
                                  excerpt=blocks[index].text[match.start():match.end()], kind="alias",
                                  basis="observation", rule="explicit-alias")
                aliases[name].append(NameVariant(name=alias, evidence=(e,)))
        for match in REFLEXIVE.finditer(surface):
            name = " ".join(match["name"].split())
            if name in known and REFLEXIVE.fullmatch(blocks[index].text[match.start():match.end()]):
                e = PriorEvidence(block=index, start=match.start(), end=match.end(),
                                  excerpt=blocks[index].text[match.start():match.end()], kind="pronoun",
                                  basis="inference", rule="subject-reflexive")
                pronoun = match["pronoun"]
                pronouns[name].append(PronounEvidence(pronoun=pronoun,
                    gender={"herself": "female", "himself": "male"}.get(pronoun), evidence=e))
    for name, evidence in known.items():
        for component in name.split() if " " in name else ():
            # Do not reconcile separately anchored identities on a shared token.
            if sum(component in n.split() for n in known) == 1:
                source = evidence[0]
                e = source.model_copy(update=dict(kind="alias", basis="inference", rule="unique-name-component"))
                aliases[name].append(NameVariant(name=component, evidence=(e,)))
    characters = []
    for name, evidence in sorted(known.items()):
        merged = {}
        for alias in aliases[name]:
            merged.setdefault(alias.name, []).extend(alias.evidence)
        characters.append(CharacterPrior(identity=name, evidence=tuple(evidence),
            aliases=tuple(NameVariant(name=n, evidence=tuple(e)) for n, e in sorted(merged.items())),
            pronouns=tuple(pronouns[name]),
            confidence="high" if any(e.kind == "speech-attribution" for e in evidence) else "medium"))
    return tuple(characters)


def preflight_document(path: str | Path):
    document, blocks, warnings = ingest_document(path)
    observations, candidates = collect_observations(blocks)
    return BookPreflight(schema_version=2, discovery_version="deterministic-en-2", document=document, blocks=blocks,
                         observations=observations, candidates=candidates, characters=discover_characters(blocks),
                         warnings=(*warnings, "Character discovery is incomplete; absence is not proof of non-character status"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    if args.document.resolve() == args.output.resolve():
        parser.error("Output must not overwrite the source document")
    try:
        artifact = preflight_document(args.document)
        # Validate the exact serialized boundary before writing the reusable artifact.
        value = BookPreflight.from_json(artifact.to_json()).to_json()
        args.output.write_text(value + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, ET.ParseError, BadZipFile) as exc:
        parser.exit(1, f"Preflight failed: {exc}\n")
    print(f"Wrote {len(artifact.characters)} character priors to {args.output}")


if __name__ == "__main__":
    main()
