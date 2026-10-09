"""Safe source-retrieval tests; no server, Kokoro, or evaluation input."""
import json
from pathlib import Path
import tempfile
import unittest

from book_preflight import BookPreflight, preflight_document


class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "fixtures/preflight"
        cls.poe = preflight_document(root / "poe-edgar-allen-the-cask-of-amontillado.epub")
        cls.women = preflight_document(root / "alcot-louisa-may-little-women.epub")
        cls.bell = preflight_document(root / "bellweather.md")

    def assert_sources(self, artifact, packet):
        observations = {o.observation_id: o for o in artifact.observations}
        limits = packet["limits"]
        self.assertLessEqual(len(packet["passages"]), limits["max_passages"])
        ordering = [(-p["score"], p["anchor_block"]) for p in packet["passages"]]
        self.assertEqual(sorted(ordering), ordering)
        for passage in packet["passages"]:
            self.assertEqual(sum(passage["selection_rationale"].values()), passage["score"])
            indices = [s["block"] for s in passage["sources"]]
            self.assertEqual(sorted(set(indices)), indices)
            self.assertLessEqual(len(indices), 2 * limits["context_blocks"] + 1)
            self.assertIn(passage["anchor_block"], indices)
            for source in passage["sources"]:
                block = artifact.blocks[source["block"]]
                self.assertEqual(block.locator, source["locator"])
                self.assertEqual(block.text[source["start"]:source["end"]], source["excerpt"])
                self.assertLessEqual(len(source["excerpt"]), limits["block_chars"])
                self.assertLessEqual(len(source["observation_references"]), limits["max_observations_per_block"])
                for ref in source["observation_references"]:
                    observation = observations[ref]
                    self.assertEqual(source["block"], observation.source.block)
                    self.assertGreaterEqual(observation.source.start, source["start"])
                    self.assertLessEqual(observation.source.end, source["end"])
                for match in source["surface_matches"]:
                    self.assertIn(match["observation_reference"], source["observation_references"])
                    self.assertEqual(observations[match["observation_reference"]].normalized_form, match["observed_surface"])

    def test_fortunato_description_is_selected_without_character_promotion(self):
        packet = self.poe.retrieve_evidence("candidate", "Fortunato")
        self.assertEqual(5, packet["passages"][0]["anchor_block"])
        source = next(s for s in packet["passages"][0]["sources"] if s["block"] == 5)
        self.assertIn("connoisseurship in wine", source["excerpt"])
        self.assertIn("was a man", source["excerpt"])
        self.assertIsNone(self.poe.find_character("Fortunato"))
        self.assert_sources(self.poe, packet)

    def test_meg_margaret_cross_name_and_conflicting_naming_evidence_survive(self):
        packet = self.women.retrieve_evidence("relationship", ("Meg", "Margaret"))
        anchors = {p["anchor_block"] for p in packet["passages"]}
        self.assertIn(2146, anchors)
        self.assertIn(2556, anchors)  # A child named Margaret; no identity-conflict suppression.
        source = next(s for p in packet["passages"] for s in p["sources"] if s["block"] == 2146)
        self.assertIn("he had never called her Margaret before", source["excerpt"])
        conflict = next(s for p in packet["passages"] for s in p["sources"] if s["block"] == 2556)
        self.assertIn("not to have two Megs", conflict["excerpt"])
        self.assertTrue(any(m["surface"] == "meg" and m["observed_surface"] == "Megs"
                            and m["rule"] == "literal-trailing-s" for m in conflict["surface_matches"]))
        self.assert_sources(self.women, packet)

    def test_jonah_adjacent_context_retains_addresses_and_response(self):
        for kind in ("candidate", "narrator"):
            with self.subTest(kind=kind):
                packet = self.bell.retrieve_evidence(kind, "Jonah Hart")
                self.assertEqual({51, 218}, {p["anchor_block"] for p in packet["passages"]})
                second = next(p for p in packet["passages"] if p["anchor_block"] == 218)
                sources = {s["block"]: s["excerpt"] for s in second["sources"]}
                self.assertTrue({217, 218, 219, 220} <= sources.keys())
                self.assertIn("nephew", sources[219])
                self.assertIn("me", sources[220])
                self.assert_sources(self.bell, packet)

    def test_unknown_or_absent_window_returns_explicit_empty(self):
        for kind, surfaces in (("candidate", "No Such Person"),
                               ("relationship", ("Jonah Hart", "No Such Person")),
                               ("narrator", "No Such Person")):
            with self.subTest(kind=kind):
                packet = self.bell.retrieve_evidence(kind, surfaces)
                self.assertEqual([], packet["passages"])
                self.assertEqual(0, packet["total_matching_anchors"])
                self.assertEqual("no-matching-source-window", packet["empty_reason"])
                self.assertFalse(packet["selection_clipped"])

    def test_bounds_provenance_repeatability_roundtrip_and_no_mutation(self):
        before = self.bell.to_json()
        restored = BookPreflight.from_json(before)
        bounds = dict(max_passages=1, context_blocks=1, block_chars=40, max_observations=1)
        packet = self.bell.retrieve_evidence("candidate", "Jonah Hart", **bounds)
        self.assertEqual(packet, restored.retrieve_evidence("candidate", "jonah hart", **bounds))
        self.assertEqual(packet, self.bell.retrieve_evidence("candidate", "Jonah Hart", **bounds))
        self.assertTrue(packet["selection_clipped"])
        self.assertTrue(any(s["text_clipped"] for p in packet["passages"] for s in p["sources"]))
        self.assert_sources(self.bell, packet)
        # Returned dictionaries do not expose mutable artifact internals.
        json.dumps(packet)
        packet["passages"][0]["sources"][0]["excerpt"] = "changed"
        self.assertEqual(before, self.bell.to_json())
        self.assertEqual(before, restored.to_json())

    def test_validation_is_strict_and_bounded(self):
        for kwargs in ({"max_passages": True}, {"max_passages": 0}, {"max_passages": 17},
                       {"context_blocks": -1}, {"context_blocks": 4}, {"block_chars": 31},
                       {"block_chars": 2401}, {"max_observations": 129}, {"max_observations": 1.0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.bell.retrieve_evidence("candidate", "Jonah Hart", **kwargs)
        for kind, surfaces in (("unknown", "Jonah"), ("candidate", ()),
                               ("candidate", ("Jonah", "Mara")), ("relationship", "Meg"),
                               ("relationship", ("Meg", "meg")), ("candidate", [None]),
                               ("candidate", " "), ("narrator", ("A", "B", "C", "D", "E"))):
            with self.subTest(kind=kind, surfaces=surfaces), self.assertRaises(ValueError):
                self.bell.retrieve_evidence(kind, surfaces)

    def test_relationship_window_and_narrator_tokens_are_observations_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.txt"
            path.write_text('Mira arrived.\n\nI waited.\n\nCelia left.\n\n"I am ready."', encoding="utf-8")
            artifact = preflight_document(path)
        before = artifact.to_json()
        adjacent = artifact.retrieve_evidence("relationship", ("Mira", "Celia"))
        self.assertEqual({0, 2}, {p["anchor_block"] for p in adjacent["passages"]})
        self.assertEqual([], artifact.retrieve_evidence("relationship", ("Mira", "Celia"), context_blocks=0)["passages"])
        narrator = artifact.retrieve_evidence("narrator")
        self.assertEqual([1], [p["anchor_block"] for p in narrator["passages"]])
        # Quoted first person cannot anchor an unnamed narrator search.
        self.assertEqual((), artifact.characters)
        self.assertEqual(before, artifact.to_json())
        self.assert_sources(artifact, narrator)

    def test_ties_are_source_ordered_and_query_order_is_irrelevant(self):
        left = self.women.retrieve_evidence("relationship", ("Meg", "Margaret"))
        right = self.women.retrieve_evidence("relationship", ("margaret", "MEG"))
        self.assertEqual(left, right)
        self.assert_sources(self.women, left)

    def test_plural_matching_is_lexical_and_does_not_expand_candidate_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.txt"
            path.write_text('Celia named the child Mira; there were two Miras.\n\nCelia waited.', encoding="utf-8")
            artifact = preflight_document(path)
        packet = artifact.retrieve_evidence("relationship", ("Mira", "Celia"), context_blocks=0)
        source = packet["passages"][0]["sources"][0]
        self.assertTrue(any(m["observed_surface"] == "Miras" and m["rule"] == "literal-trailing-s"
                            for m in source["surface_matches"]))
        candidate = artifact.retrieve_evidence("candidate", "Mira", context_blocks=0)
        self.assertFalse(any(m["observed_surface"] == "Miras" for p in candidate["passages"]
                             for s in p["sources"] for m in s["surface_matches"]))
        self.assert_sources(artifact, packet)


if __name__ == "__main__":
    unittest.main()
