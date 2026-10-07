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
            lambda d: d.update(schema_version=2),
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
            invalid["schema_version"] = 2
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


if __name__ == "__main__":
    unittest.main()
