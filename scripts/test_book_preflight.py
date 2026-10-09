"""Safe offline ingestion/artifact and runtime-prior regression tests."""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
from pydantic import ValidationError

from book_preflight import BookPreflight, preflight_document

fake_kokoro = types.ModuleType("kokoro")
fake_kokoro.KPipeline = lambda *args, **kwargs: None
sys.modules["kokoro"] = fake_kokoro
import server


def text_pdf():
    """Tiny real two-page text PDF; no fixture-generation dependency."""
    stream = b'BT /F1 12 Tf 72 720 Td (Mara said, "Wait.") Tj ET'
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> >>',
        b'<< /Title (Test Book) /Author (Test Author) >>',
    ]
    raw, offsets = b'%PDF-1.4\n', [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += str(index).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    xref = len(raw)
    raw += b'xref\n0 8\n0000000000 65535 f \n'
    raw += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return raw + f'trailer\n<< /Size 8 /Root 1 0 R /Info 7 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prior_patch = patch.object(server, "ACTIVE_BOOK_PRIOR", None)
        self.prior_patch.start()
        self.addCleanup(self.prior_patch.stop)
        server.LITERARY_STATES.clear()

    def artifact(self, text='Mara said, "Wait."\n\nElias replied, "No."', name="book.txt"):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return preflight_document(path)

    def process(self, text, client="reader"):
        entry = server.prepare_segment(client, text, server.DEFAULT_VOICE)
        server.record_segment(client, entry)
        return entry

    def attach(self, artifact=None):
        artifact = artifact or self.artifact()
        server.LITERARY_STATES["reader"] = server.LiteraryState.for_book(artifact)
        return server.LITERARY_STATES["reader"]

    def test_txt_structure_identity_encoding_and_repeatability(self):
        artifact = self.artifact()
        self.assertEqual(2, len(artifact.blocks))
        self.assertEqual(["Elias", "Mara"], [c.identity for c in artifact.characters])
        self.assertEqual(artifact.to_json(), preflight_document(self.root / "book.txt").to_json())
        path = self.root / "unicode.TXT"
        path.write_bytes('Élise said, "Oui."'.encode("utf-16"))
        self.assertEqual("Élise", preflight_document(path).characters[0].identity)

    def test_markdown_headings_links_and_code_exclusion(self):
        artifact = self.artifact('# A Story\n*Mara* said, "Wait."\n\n```python\nFake said hello\n```\n\n`Ghost said hello`\n\n[Elias](https://example.com) replied, "No."', "book.md")
        self.assertEqual("A Story", artifact.document.title)
        self.assertEqual("heading", artifact.blocks[0].kind)
        self.assertEqual(["Elias", "Mara"], [c.identity for c in artifact.characters])
        self.assertNotIn("https", artifact.to_json())

    def test_epub_spine_order_metadata_and_readable_blocks(self):
        path = self.root / "book.epub"
        with ZipFile(path, "w") as archive:
            archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
            archive.writestr("OPS/book.opf", '''<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>The Test</dc:title><dc:creator>Author</dc:creator><dc:identifier>book-id</dc:identifier><dc:language>en</dc:language></metadata><manifest><item id="two" href="two.xhtml" media-type="application/xhtml+xml"/><item id="one" href="one.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>''')
            archive.writestr("OPS/two.xhtml", '<html><body><p>Elias replied, "No."</p></body></html>')
            archive.writestr("OPS/one.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Ignore</title></head><body><nav><p>Fake said hi</p></nav><h1>Opening</h1><p>Mara <em>said</em>, “Wait.”</p><script>Ghost said hello</script></body></html>')
        artifact = preflight_document(path)
        self.assertEqual(("The Test", ("Author",), ("book-id",), "en"),
                         (artifact.document.title, artifact.document.authors, artifact.document.identifiers, artifact.document.language))
        self.assertEqual(["Opening", "Mara said, “Wait.”", 'Elias replied, "No."'], [b.text for b in artifact.blocks])
        self.assertEqual(["Elias", "Mara"], [c.identity for c in artifact.characters])
        self.assertTrue(artifact.blocks[0].locator.startswith("OPS/one.xhtml"))

    def test_pdf_real_text_metadata_pages_and_empty_page_warning(self):
        path = self.root / "book.pdf"
        path.write_bytes(text_pdf())
        artifact = preflight_document(path)
        self.assertEqual("Test Book", artifact.document.title)
        self.assertEqual(("Test Author",), artifact.document.authors)
        self.assertEqual("page:1", artifact.blocks[0].locator)
        self.assertEqual("page", artifact.blocks[0].kind)
        self.assertEqual("Mara", artifact.characters[0].identity)
        self.assertTrue(any("Page 2" in w for w in artifact.warnings))

    def test_pdf_empty_and_encrypted_fail_explicitly(self):
        from pypdf import PdfWriter
        for encrypted in (False, True):
            path = self.root / "empty.pdf"
            writer = PdfWriter()
            writer.add_blank_page(100, 100)
            if encrypted:
                writer.encrypt("secret")
            writer.write(path)
            with self.assertRaisesRegex(ValueError, "Encrypted|no readable text"):
                preflight_document(path)

    def test_unsupported_and_empty_documents_fail(self):
        for name, text in (("book.docx", "hello"), ("book.txt", " \n\n")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.artifact(text, name)

    def test_discovery_excludes_quotes_environment_and_single_weak_action(self):
        artifact = self.artifact('“Fake said that Mara nodded.”\n\nWater flowed over the stones. Thunder rolled.\n\nUnknown crouched.\n\nElias frowned. Elias nodded.\n\nMara said, "Wait."')
        self.assertEqual(["Elias", "Mara"], [c.identity for c in artifact.characters])
        self.assertEqual("medium", artifact.find_character("Elias").confidence)
        self.assertEqual("inference", artifact.find_character("Mara").status_basis)
        self.assertEqual("observation", artifact.find_character("Mara").evidence[0].basis)

    def test_quotes_continue_across_blocks(self):
        artifact = self.artifact('“Listen.\n\nFake said hi.” Mara replied.')
        self.assertEqual(["Mara"], [c.identity for c in artifact.characters])

    def test_aliases_full_names_and_supported_pronouns(self):
        artifact = self.artifact('Mara Vale said, "Wait."\n\nMara Vale, also known as Red, returned.\n\nMara Vale introduced herself.\n\nElias replied, "No."\n\nElias saw her coat.')
        mara = artifact.find_character("Mara")
        self.assertEqual("Mara Vale", mara.identity)
        self.assertEqual(mara, artifact.find_character("Red"))
        self.assertEqual(["female"], [p.gender for p in mara.pronouns])
        self.assertEqual((), artifact.find_character("Elias").pronouns)
        self.assertEqual("inference", mara.pronouns[0].evidence.basis)

    def test_ambiguous_alias_does_not_validate_candidate(self):
        artifact = self.artifact('Mara said, "Yes." Elias said, "No."\n\nMara, also known as Red, smiled.\n\nElias, also known as Red, frowned.')
        self.assertIsNone(artifact.find_character("Red"))

    def test_artifact_round_trip_and_deep_immutability(self):
        artifact = self.artifact()
        self.assertEqual(artifact, BookPreflight.from_json(artifact.to_json()))
        with self.assertRaises(ValidationError):
            artifact.characters[0].confidence = "medium"
        with self.assertRaises(ValidationError):
            artifact.document.title = "changed"

    def test_invalid_artifact_versions_spans_identities_and_traits_rejected(self):
        base = json.loads(self.artifact('Mara said, "Wait."\n\nMara introduced herself.').to_json())
        mutations = [
            lambda d: d.update(schema_version=99),
            lambda d: d.update(schema_version=True),
            lambda d: d.pop("schema_version"),
            lambda d: d.update(extra=True),
            lambda d: d["document"].update(sha256="bad"),
            lambda d: d["characters"][0].update(identity="__narrator__"),
            lambda d: d["characters"][0].update(identity="Elias"),
            lambda d: d["characters"][0].update(evidence=[]),
            lambda d: d["characters"][0]["evidence"][0].update(block=900),
            lambda d: d["characters"][0]["evidence"][0].update(excerpt="made up"),
            lambda d: d["characters"][0]["evidence"][0].update(basis="inference"),
            lambda d: d["characters"][0]["pronouns"][0].update(gender="male"),
            lambda d: d["characters"].append(d["characters"][0]),
        ]
        for index, mutate in enumerate(mutations):
            value = json.loads(json.dumps(base))
            mutate(value)
            with self.subTest(index=index), self.assertRaises(ValueError):
                BookPreflight.from_json(json.dumps(value))

    def test_cli_writes_valid_artifact_and_protects_source(self):
        from book_preflight import main
        source = self.root / "book.txt"
        self.artifact()
        output = self.root / "book.json"
        with patch.object(sys, "argv", ["preflight", str(source), "-o", str(output)]), patch("builtins.print"):
            main()
        self.assertEqual(self.artifact(), BookPreflight.from_json(output.read_text(encoding="utf-8")))
        with patch.object(sys, "argv", ["preflight", str(source), "-o", str(source)]), self.assertRaises(SystemExit):
            main()

    def test_priors_do_not_seed_scene_performance_or_runtime_serialization(self):
        state = self.attach()
        self.assertEqual([], list(state.scene.recent_entities))
        self.assertEqual([], list(state.scene.participants))
        self.assertEqual({}, state.performance.voices)
        entry = self.process('"Who is there?"')
        self.assertIsNone(entry.decision.speaker)
        self.assertIsNone(entry.performance_decision.speaker)
        restored = server.LiteraryState.from_json(state.to_json())
        self.assertIsNone(restored.book_prior)
        self.assertEqual(state.model_dump(), restored.model_dump())

    def test_water_thunder_and_unknown_actions_are_rejected_with_prior(self):
        for text in ("Water flowed.", "Thunder rolled.", "Stranger crouched."):
            with self.subTest(text=text):
                state = self.attach()
                # An old or externally supplied performance guess is no bypass.
                state.performance.voices[text.split()[0]] = "af_heart"
                entry = self.process(text)
                self.assertIsNone(entry.narrative_actor)
                following = self.process('"Wait."')
                self.assertEqual((), following.decision.evidence)
                self.assertIsNone(following.performance_decision.speaker)
                self.assertEqual([], list(state.scene.recent_entities))

    def test_validated_actor_alias_is_evidence_and_performance_stays_separate(self):
        state = self.attach(self.artifact('Mara said, "Wait."\n\nMara, also known as Red, smiled.'))
        before = state.book_prior.to_json()
        action = self.process("Red crouched.")
        self.assertEqual("Red", action.narrative_actor)
        line = self.process('"Here."')
        self.assertEqual("unresolved", line.decision.status)
        self.assertEqual("Red", line.performance_decision.speaker)
        self.assertEqual([], list(state.scene.participants))
        self.assertEqual(before, state.book_prior.to_json())

    def test_current_explicit_evidence_wins_for_absent_and_weather_characters(self):
        state = self.attach()
        for name in ("Stranger", "Water", "Thunder"):
            explicit = self.process(f'"Hello," {name} said.')
            self.assertEqual(name, explicit.decision.speaker)
            self.assertEqual("resolved", explicit.decision.status)
            self.assertEqual(name, self.process(f"{name} crouched.").narrative_actor)
        self.assertIsNone(state.book_prior.find_character("Water"))
        self.assertEqual("Nova", self.process("Nova said.").narrative_actor)

    def test_prior_traits_never_lock_or_override_current_traits(self):
        state = self.attach(self.artifact('Mara said, "Wait." Mara introduced herself.'))
        self.assertEqual({}, state.scene.gender)
        self.process("Mara raised his hand.")
        self.assertEqual("male", state.scene.gender["Mara"])
        self.assertEqual("female", state.book_prior.find_character("Mara").pronouns[0].gender)

    def test_without_prior_existing_actor_behavior_is_preserved(self):
        self.assertEqual("Water", self.process("Water flowed.").narrative_actor)

    def test_http_attach_validation_shared_selection_switch_detach_and_reset(self):
        artifact = self.artifact()
        self.process('"Hello," Other said.')
        old_scope = server.LITERARY_STATES["reader"].scope_id
        with TestClient(server.app, client=("reader", 5000)) as client, patch("builtins.print"):
            response = client.post("/book/prior", json=json.loads(artifact.to_json()))
            self.assertEqual(200, response.status_code)
            state = server.LITERARY_STATES["reader"]
            self.assertNotEqual(old_scope, state.scope_id)
            self.assertEqual({}, state.performance.voices)
            self.assertEqual(json.loads(artifact.to_json()), client.get("/book/prior").json()["prior"])
            invalid = json.loads(artifact.to_json())
            invalid["schema_version"] = True
            self.assertEqual(422, client.post("/book/prior", json=invalid).status_code)
            self.assertIs(state, server.LITERARY_STATES["reader"])
            self.assertEqual(artifact, server.LITERARY_STATES["another"].book_prior)
            self.assertIsNot(state, server.LITERARY_STATES["another"])
            replacement = self.artifact('Nova said, "Hi."', "other.txt")
            client.post("/book/prior", json=json.loads(replacement.to_json()))
            self.assertNotIn("another", server.LITERARY_STATES)
            self.assertIsNone(server.LITERARY_STATES["reader"].book_prior.find_character("Mara"))
            self.assertNotEqual(state.scope_id, server.LITERARY_STATES["reader"].scope_id)
            client.delete("/book/prior")
            self.assertIsNone(client.get("/book/prior").json()["prior"])
            client.post("/book/prior", json=json.loads(artifact.to_json()))
            client.post("/context/reset")
            self.assertEqual(json.loads(artifact.to_json()), client.get("/book/prior").json()["prior"])
            self.assertEqual(artifact, server.LITERARY_STATES["reader"].book_prior)

    def test_localhost_management_prior_is_used_by_existing_and_new_tts_clients(self):
        import numpy as np
        artifact = self.artifact()
        self.process('I said, "Old.', "192.168.1.50")
        old_scope = server.LITERARY_STATES["192.168.1.50"].scope_id
        with TestClient(server.app, client=("127.0.0.1", 5000)) as manager, \
                TestClient(server.app, client=("192.168.1.50", 5000)) as pocketbook, \
                TestClient(server.app, client=("192.168.1.51", 5000)) as later_client, \
                patch.object(server, "synthesize", return_value=np.ones(100, dtype=np.float32)), \
                patch("builtins.print"):
            response = manager.post("/book/prior", json=json.loads(artifact.to_json()))
            self.assertEqual("server", response.json()["attachment_scope"])
            self.assertNotIn("192.168.1.50", server.LITERARY_STATES)
            for client in (pocketbook, later_client):
                selection = client.get("/book/prior").json()
                self.assertEqual("server", selection["attachment_scope"])
                self.assertEqual(json.loads(artifact.to_json()), selection["prior"])
                response = client.post("/v1/audio/speech", json={"input": 'Water flowed.\nThunder rolled.\nMara crouched.\n"Here."'})
                self.assertEqual(200, response.status_code)
                entries = client.get("/context").json()["utterances"]
                self.assertEqual([None, None, "Mara", None], [e["narrative_actor"] for e in entries])
                self.assertEqual("unresolved", entries[-1]["decision"]["status"])
                self.assertEqual("Mara", entries[-1]["performance_decision"]["speaker"])
                cast = client.get("/cast").json()
                self.assertEqual([], cast["participants"])
                self.assertIsNone(cast["pov_entity_id"])
            state = server.LITERARY_STATES["192.168.1.50"]
            self.assertNotEqual(old_scope, state.scope_id)
            self.assertEqual(artifact.to_json(), server.ACTIVE_BOOK_PRIOR.to_json())
            # Local management diagnostics do not borrow PocketBook's turns.
            self.assertEqual([], manager.get("/context").json()["utterances"])
            self.assertNotEqual(state.scope_id, server.LITERARY_STATES["192.168.1.51"].scope_id)

    def test_cross_client_replacement_and_detach_reset_every_scope(self):
        first = self.artifact()
        second = self.artifact('Nova said, "Hi."', "second.txt")
        with TestClient(server.app, client=("127.0.0.1", 5000)) as manager:
            manager.post("/book/prior", json=json.loads(first.to_json()))
            self.process('Mara said, "Wait.', "pocketbook")
            self.process('"Hello," I said.', "other")
            old_scope = server.LITERARY_STATES["pocketbook"].scope_id
            manager.post("/book/prior", json=json.loads(second.to_json()))
            self.assertNotIn("pocketbook", server.LITERARY_STATES)
            self.assertNotIn("other", server.LITERARY_STATES)
            state = server.LITERARY_STATES["pocketbook"]
            self.assertNotEqual(old_scope, state.scope_id)
            self.assertEqual(second, state.book_prior)
            self.assertFalse(state.scene.quote_open)
            self.assertEqual({}, state.performance.voices)
            self.assertEqual([], list(state.scene.participants))
            self.assertEqual(0, state.position)
            self.assertIsNone(self.process("Mara crouched.", "pocketbook").narrative_actor)
            self.assertEqual("Nova", self.process("Nova crouched.", "pocketbook").narrative_actor)
            manager.delete("/book/prior")
            self.assertEqual({}, server.LITERARY_STATES)
            self.assertIsNone(manager.get("/book/prior").json()["prior"])
            self.assertEqual({}, server.LITERARY_STATES)  # GET is read-only.
            detached = server.LITERARY_STATES["pocketbook"]
            self.assertIsNone(detached.book_prior)
            self.assertEqual([], list(detached.context))
            self.assertEqual("Water", self.process("Water flowed.", "pocketbook").narrative_actor)

    def test_invalid_cross_client_attachment_preserves_selection_and_all_states(self):
        artifact = self.artifact()
        with TestClient(server.app, client=("127.0.0.1", 5000)) as manager:
            manager.post("/book/prior", json=json.loads(artifact.to_json()))
            self.process('"Wait," Mara said.', "pocketbook")
            state = server.LITERARY_STATES["pocketbook"]
            prior = server.ACTIVE_BOOK_PRIOR
            invalid = json.loads(artifact.to_json())
            invalid["schema_version"] = 99
            self.assertEqual(422, manager.post("/book/prior", json=invalid).status_code)
            self.assertIs(prior, server.ACTIVE_BOOK_PRIOR)
            self.assertIs(state, server.LITERARY_STATES["pocketbook"])
            self.assertEqual(1, len(state.context))

    def test_client_context_reset_retains_active_prior_and_other_client_state(self):
        artifact = self.artifact()
        with TestClient(server.app, client=("127.0.0.1", 5000)) as manager, \
                TestClient(server.app, client=("pocketbook", 5000)) as pocketbook:
            manager.post("/book/prior", json=json.loads(artifact.to_json()))
            self.process('I said, "Wait.', "pocketbook")
            self.process('"Hi," Mara said.', "other")
            old_scope = server.LITERARY_STATES["pocketbook"].scope_id
            other_state = server.LITERARY_STATES["other"]
            pocketbook.post("/context/reset")
            state = server.LITERARY_STATES["pocketbook"]
            self.assertEqual(artifact, state.book_prior)
            self.assertNotEqual(old_scope, state.scope_id)
            self.assertEqual({}, state.performance.voices)
            self.assertFalse(state.scene.quote_open)
            self.assertIsNone(state.scene.pov_entity_id)
            self.assertIs(other_state, server.LITERARY_STATES["other"])

    def test_observation_validation_and_candidate_references_are_strict(self):
        artifact = self.artifact('Mara said, "Wait." Mara introduced herself.')
        base = json.loads(artifact.to_json())
        mutations = [
            lambda d: d.update(discovery_version="deterministic-en-1"),
            lambda d: d.pop("discovery_version"),
            lambda d: d.pop("candidates"),
            lambda d: d.pop("observations"),
            lambda d: d["observations"][0].update(observation_id="o99"),
            lambda d: d["observations"][0].update(basis="inference"),
            lambda d: d["observations"][0].update(normalized_form="Elias"),
            lambda d: d["observations"][0].update(in_dialogue=True),
            lambda d: d["observations"][0]["source"].update(locator="foreign"),
            lambda d: d["observations"][0]["source"].update(end=9999),
            lambda d: d["observations"][0]["source"].update(excerpt="invented"),
            lambda d: d["candidates"][0].update(occurrences=["o999999"]),
            lambda d: d["candidates"][0].update(occurrences=["o1"]),
            lambda d: d["candidates"][0].update(surface="When Laurie"),
            lambda d: d["candidates"][0].update(status="resolved"),
            lambda d: d.update(candidates=[]),
            lambda d: d["observations"].append(d["observations"][0]),
        ]
        for index, mutate in enumerate(mutations):
            value = json.loads(json.dumps(base))
            mutate(value)
            with self.subTest(index=index), self.assertRaises(ValueError):
                BookPreflight.from_json(json.dumps(value))
        self.assertEqual(artifact, BookPreflight.from_json(artifact.to_json()))

    def test_clean_subjects_possessives_and_ambiguous_name_phrases(self):
        artifact = self.artifact('When Laurie said hello. Presently Jo said hello. Tell Beth Frank asked for her. Neither said a word. Laurie’s eyes smiled. Laurie’s coat was wet. Laurie’s English friends arrived. Mary Jane asked about O’Connor. O’Connor nodded.')
        names = {c.identity for c in artifact.characters}
        candidates = {c.surface for c in artifact.candidates}
        for malformed in ("When Laurie", "Presently Jo", "Tell Beth Frank", "Laurie’s", "Laurie English", "Neither"):
            self.assertNotIn(malformed, names | candidates)
        self.assertTrue({"Laurie", "Jo", "Frank", "Mary Jane"} <= names)
        self.assertTrue({"Beth", "Frank", "Laurie", "O’Connor"} <= candidates)
        source_forms = [o.source.excerpt for o in artifact.observations if o.normalized_form == "Laurie"]
        self.assertIn("Laurie’s", source_forms)

    def test_no_name_or_reference_anchor_bridges_a_quotation(self):
        artifact = self.artifact('Mara "Elias is here" said nothing.\n\nI "Come home" said no more.')
        self.assertEqual((), artifact.characters)
        self.assertFalse(any(o.kind == "speech-attribution" for o in artifact.observations))

    def test_lexical_boundaries_use_source_casing_without_losing_embedded_names(self):
        artifact = self.artifact('We spoke about Mira and could call her here. A poor singer was dearest to me.\n\nAbout Mira. Call Mira. Here Mira waited. Poor Mira. My Dearest Celia.\n\nMira visited Celia. I’m ready. Do it. O’Connor arrived. Mira’s coat dried.')
        names = {c.surface for c in artifact.candidates}
        self.assertFalse({"About", "Call", "Here", "Poor", "Dearest", "My", "Do", "I’m",
                          "About Mira", "Call Mira", "Here Mira", "Poor Mira", "My Dearest Celia"} & names)
        self.assertTrue({"Mira", "Celia", "O’Connor"} <= names)
        # Every embedded name keeps its own exact source span, even when the
        # surrounding capitalized phrase is rejected as a lexical hypothesis.
        for phrase, name in (("About Mira", "Mira"), ("Call Mira", "Mira"),
                             ("Here Mira", "Mira"), ("Poor Mira", "Mira"),
                             ("My Dearest Celia", "Celia")):
            block = next(i for i, b in enumerate(artifact.blocks) if phrase in b.text)
            start = artifact.blocks[block].text.index(phrase) + phrase.index(name)
            self.assertTrue(any(o.kind == "name-mention" and o.normalized_form == name
                                and (o.source.block, o.source.start, o.source.end) == (block, start, start + len(name))
                                for o in artifact.observations))
        self.assertTrue(any(o.source.excerpt == "Mira’s" and o.normalized_form == "Mira"
                            for o in artifact.observations))

    def test_lexical_boundaries_preserve_ambiguous_names_and_multiword_spans(self):
        artifact = self.artifact('We may march with grace and hope.\n\nMay said hello. March asked why. Grace replied quietly. Hope smiled.\n\nI met Mary Jane and Hope today. Mary Jane left. Newcomer waited.')
        names = {c.surface for c in artifact.candidates}
        self.assertTrue({"May", "March", "Grace", "Hope", "Mary", "Jane", "Mary Jane", "Newcomer"} <= names)
        self.assertEqual({"May", "March", "Grace"}, {c.identity for c in artifact.characters})
        self.assertIsNone(artifact.find_character("Hope"))

    def test_lexical_boundaries_do_not_join_headings_newlines_or_cue_subjects(self):
        artifact = self.artifact('# Mary Jane\n\nCHAPTER NINE MIRA GOES HOME\n\nMira waited.\n\nCelia\nMira arrived.\n\nTell Beth Frank asked for her.', name="book.md")
        names = {c.surface for c in artifact.candidates}
        self.assertFalse({"Mary Jane", "NINE MIRA GOES", "GOES", "Celia Mira", "Beth Frank", "Tell Beth Frank"} & names)
        self.assertTrue({"Mira", "Celia", "Beth", "Frank"} <= names)
        self.assertEqual({"Frank"}, {c.identity for c in artifact.characters})

    def test_candidate_evidence_never_validates_a_runtime_character(self):
        artifact = self.artifact('Fortunato walked beside me. He wore a cloak.')
        self.assertTrue(any(c.surface == "Fortunato" for c in artifact.candidates))
        self.assertIsNone(artifact.find_character("Fortunato"))
        state = self.attach(artifact)
        before = state.book_prior.to_json()
        self.assertIsNone(self.process("Fortunato crouched.").narrative_actor)
        self.assertEqual({}, state.performance.voices)
        self.assertEqual(before, state.book_prior.to_json())

    def test_gutenberg_markers_are_source_aware_and_preserve_metadata_and_locators(self):
        artifact = self.artifact('Publisher One said hello.\n\n*** START OF THE PROJECT GUTENBERG EBOOK A TEST ***\n\nMara said, "Project Gutenberg published my diary."\n\n*** END OF THE PROJECT GUTENBERG EBOOK A TEST ***\n\nPublisher Two said goodbye.')
        self.assertEqual(["txt:3"], [b.locator for b in artifact.blocks])
        self.assertEqual(["Mara"], [c.identity for c in artifact.characters])
        self.assertIn("Project Gutenberg", artifact.blocks[0].text)
        self.assertEqual("book.txt", artifact.document.filename)
        self.assertTrue(any("Excluded 4" in w for w in artifact.warnings))
        unmarked = self.artifact('A story about Project Gutenberg.\n\nMara said, "Hello."')
        self.assertEqual(2, len(unmarked.blocks))

    def test_schema_one_attachment_is_rejected_without_changing_active_prior(self):
        legacy_path = Path(__file__).resolve().parents[1] / "fixtures/preflight/topology-smoke.preflight.json"
        legacy = legacy_path.read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "regenerate"):
            BookPreflight.from_json(legacy)
        current = self.artifact()
        with TestClient(server.app) as manager:
            self.assertEqual(200, manager.post("/book/prior", json=json.loads(current.to_json())).status_code)
            prior = server.ACTIVE_BOOK_PRIOR
            self.process('"Hi," Mara said.', "pocketbook")
            state = server.LITERARY_STATES["pocketbook"]
            self.assertEqual(422, manager.post("/book/prior", json=json.loads(legacy)).status_code)
            self.assertIs(prior, server.ACTIVE_BOOK_PRIOR)
            self.assertIs(state, server.LITERARY_STATES["pocketbook"])


class CorpusEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1] / "fixtures/preflight"
        # Evaluation ground truth is never an input to preflight.
        cls.poe = preflight_document(cls.root / "poe-edgar-allen-the-cask-of-amontillado.epub")
        cls.little_women = preflight_document(cls.root / "alcot-louisa-may-little-women.epub")
        cls.bellweather = preflight_document(cls.root / "bellweather.md")

    def test_poe_retains_fortunato_mentions_dialogue_and_unbound_pronouns(self):
        artifact = self.poe
        candidate = next(c for c in artifact.candidates if c.surface == "Fortunato")
        self.assertEqual(("inference", "unassessed", 14), (candidate.basis, candidate.status, len(candidate.occurrences)))
        self.assertEqual((), artifact.characters)
        observations = {o.observation_id: o for o in artifact.observations}
        first = observations[candidate.occurrences[0]]
        self.assertEqual((25, 34, "Fortunato"), (first.source.start, first.source.end, first.source.excerpt))
        self.assertTrue(first.source.locator.endswith("1063-h-0.htm.xhtml:14"))
        self.assertTrue(any(observations[ref].in_dialogue for ref in candidate.occurrences))
        packet = artifact.candidate_packet("Fortunato")
        nearby = packet["occurrences"][0]["nearby_observations"]
        self.assertTrue({"I", "he"} <= {o["source"]["excerpt"] for o in nearby if o["kind"] == "pronoun-mention"})
        self.assertTrue(any(o.rule == "reference-speech" and o.source.excerpt == "said he" for o in artifact.observations))

    def test_little_women_malformed_forms_are_absent_but_clean_evidence_remains(self):
        artifact = self.little_women
        names = {c.surface for c in artifact.candidates} | {c.identity for c in artifact.characters}
        self.assertFalse({"When Laurie", "Presently Jo", "Tell Beth Frank", "Laurie’s", "Neither",
                          "About Meg", "Call Meg", "Here Meg", "Poor Meg", "My Dearest Margaret",
                          "NINE MEG GOES", "I’m", "Do"} & names)
        for anchor in ("Laurie said", "Jo said", "Frank asked"):
            self.assertTrue(any(o.kind == "speech-attribution" and o.source.excerpt == anchor for o in artifact.observations))
        self.assertTrue(any(o.source.excerpt == "Laurie’s" and o.normalized_form == "Laurie" for o in artifact.observations))
        self.assertTrue({"Laurie", "Jo", "Frank", "Beth", "Meg", "Amy"} <= names)
        occurrences = {c.surface: len(c.occurrences) for c in artifact.candidates}
        for name, count in (("Meg", 683), ("Margaret", 22), ("Laurie", 596), ("Jo", 1355),
                            ("Frank", 18), ("Amy", 645), ("Beth", 459)):
            self.assertEqual(count, occurrences[name], name)
        self.assertEqual({"Amy", "Annie", "Aunt March", "Belle", "Beth", "Clara", "Demi", "Esther",
                          "Father", "Frank", "Fred", "Grace", "Hannah", "Jo", "John", "Kate", "Kirke",
                          "Laurie", "Major Lincoln", "Mamma", "March", "Margaret", "May", "Meg", "Minnie",
                          "Nan", "Ned", "Sallie", "Tudor", "Zara"}, {c.identity for c in artifact.characters})

    def test_bellweather_preserves_secondary_and_narrator_evidence_without_promotion(self):
        artifact = self.bellweather
        names = {c.surface for c in artifact.candidates}
        self.assertTrue({"Jonah", "Jonah Hart", "Lillian", "Eleanor Bell", "Samuel Ward", "Silas Bell"} <= names)
        self.assertEqual({"Mara", "Daniel", "Evelyn", "Rose", "Tomas"}, {c.identity for c in artifact.characters})
        self.assertIsNone(artifact.find_character("Jonah Hart"))
        self.assertFalse({"I’m", "Do"} & names)
        occurrences = {c.surface: len(c.occurrences) for c in artifact.candidates}
        for name, count in (("Jonah Hart", 2), ("Eleanor Bell", 7), ("Samuel Ward", 4), ("Silas Bell", 1)):
            self.assertEqual(count, occurrences[name], name)
        self.assertTrue(any(o.kind == "pronoun-mention" and o.source.excerpt == "I" and not o.in_dialogue for o in artifact.observations))

    def test_corpus_boilerplate_removal_retains_work_and_metadata(self):
        for artifact, title, author, removed, block_count in (
                (self.poe, "The Cask of Amontillado", "Edgar Allan Poe", 63, 92),
                (self.little_women, "Little Women", "Louisa May Alcott", 60, 4171)):
            with self.subTest(title=title):
                self.assertEqual(title, artifact.document.title)
                self.assertEqual((author,), artifact.document.authors)
                self.assertEqual(block_count, len(artifact.blocks))
                self.assertTrue(any(f"Excluded {removed} " in w for w in artifact.warnings))
                self.assertFalse(any("THE FULL PROJECT GUTENBERG" in b.text or "*** START OF THE PROJECT GUTENBERG" in b.text for b in artifact.blocks))
                self.assertFalse(any(c.surface == "Gutenberg" for c in artifact.candidates))
        self.assertIn("In pace requiescat", self.poe.blocks[-1].text)
        self.assertIn("my girls", self.little_women.blocks[-1].text.lower())

    def test_full_artifact_roundtrip_and_bounded_model_ready_packet(self):
        for artifact, surface in ((self.poe, "Fortunato"), (self.little_women, "Laurie")):
            with self.subTest(surface=surface):
                restored = BookPreflight.from_json(artifact.to_json())
                self.assertEqual(artifact, restored)
                packet = restored.candidate_packet(surface, max_occurrences=2, max_nearby=3, context_chars=80)
                self.assertEqual(2, len(packet["occurrences"]))
                for occurrence in packet["occurrences"]:
                    self.assertLessEqual(len(occurrence["nearby_observations"]), 3)
                    self.assertLessEqual(len(occurrence["context"]["excerpt"]), 160 + len(occurrence["occurrence"]["source"]["excerpt"]))
                    for cue in occurrence["nearby_observations"]:
                        self.assertGreaterEqual(cue["source"]["start"], occurrence["context"]["start"])
                        self.assertLessEqual(cue["source"]["end"], occurrence["context"]["end"])
                for kwargs in ({"max_occurrences": 9}, {"max_nearby": 0}, {"context_chars": True}):
                    with self.assertRaises(ValueError):
                        restored.candidate_packet(surface, **kwargs)
        with self.assertRaises(ValueError):
            self.poe.candidate_packet("Nobody")


if __name__ == "__main__":
    unittest.main()
