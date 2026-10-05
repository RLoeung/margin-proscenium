import unittest
import sys
import types


# Routing tests must not initialize or download the speech model.
fake_kokoro = types.ModuleType("kokoro")
fake_kokoro.KPipeline = lambda *args, **kwargs: None
sys.modules["kokoro"] = fake_kokoro

import server


class RoutingRegressionTests(unittest.TestCase):
    def setUp(self):
        server.CONTEXTS.clear()
        server.DIALOGUE_STATE.clear()
        server.CAST_STATE.clear()

    def process(self, client, text):
        classification = server.classify_utterance(client, text)
        actor = None if classification["is_dialogue"] else server.observe_narrative_actor(client, text)
        speaker, _, reason, _ = server.infer_speaker(client, text, classification)
        entry = {"text": text, **classification, "narrative_actor": actor,
                 "cast_speaker": speaker, "routing_reason": reason}
        server.CONTEXTS[client].append(entry)
        narrative_reason = server.update_scene_after_narration(client, text, classification, actor)
        if narrative_reason:
            entry["routing_reason"] = narrative_reason
        return entry

    def test_words_inside_dialogue_are_not_attributions(self):
        samples = [
            "“You say that about all my good ideas.”",
            '"I say that about all your ideas."',
            "“For the record, I agree with him.”",
            '"I said give it to me."',
            "“Called who?”",
            '"You said that last time too."',
        ]
        for index, text in enumerate(samples):
            client = f"false-attribution-{index}"
            entry = self.process(client, text)
            self.assertFalse(entry["has_attribution"])
            self.assertIsNone(entry["speaker_name"])
            self.assertNotEqual(entry["type"], "dialogue_with_attribution")
            state = server.CAST_STATE[client]
            self.assertEqual({}, state["voices"])
            self.assertEqual([], list(state["recent_entities"]))

    def test_real_pre_and_post_quote_attributions_survive(self):
        cases = [
            ("“Traitor,” Mara said.", "Mara"),
            ('Mara said, "Traitor."', "Mara"),
            ("“Traitor,” she said.", "Mara"),
            ('"I\'m sure," I said.', "__narrator__"),
        ]
        for index, (text, expected) in enumerate(cases):
            client = f"valid-attribution-{index}"
            if "she said" in text:
                server.remember_entity(client, "Mara")
                server.refine_gender(client, "Mara", "female")
                server.CAST_STATE[client]["pronouns"]["she"] = "Mara"
            entry = self.process(client, text)
            self.assertTrue(entry["has_attribution"])
            self.assertEqual(expected, entry["cast_speaker"])

    def test_open_quote_split_does_not_scan_continuation_as_attribution(self):
        client = "split-quote"
        first = self.process(client, "“H.")
        second = self.process(client, "Vale said this was safe.”")
        self.assertTrue(first["quote_open_after"])
        self.assertFalse(second["has_attribution"])
        self.assertIsNone(second["speaker_name"])
        self.assertEqual(first["cast_speaker"], second["cast_speaker"])
        self.assertEqual("quote-span-continuation", second["routing_reason"])

    def test_non_entity_starters_do_not_create_action_cues(self):
        samples = [
            "Another impact rattled the roof.", "Then the glass cracked.",
            "There was a second impact.", "Something moved beyond the door.",
            "At dawn the rain stopped.", "Closer inspection revealed a seam.",
            "Despite the noise, nobody moved.", "Neither answer helped.",
            "Nobody spoke.", "Or perhaps it was only wind.",
        ]
        for index, text in enumerate(samples):
            client = f"false-actor-{index}"
            entry = self.process(client, text)
            self.assertIsNone(entry["narrative_actor"])
            self.assertIsNone(server.CAST_STATE[client]["likely_next_speaker"])
            following = self.process(client, "“Who is there?”")
            self.assertEqual("conservative-dialogue-fallback", following["routing_reason"])
            self.assertEqual([], list(server.CAST_STATE[client]["recent_entities"]))

    def test_valid_named_actors_still_work(self):
        for index, text in enumerate((
            "Mara laughed.", "Elias frowned.",
            "Mara pushed through the warped door first.",
        )):
            client = f"valid-actor-{index}"
            entry = self.process(client, text)
            expected = text.split()[0]
            self.assertEqual(expected, entry["narrative_actor"])
            self.assertEqual(expected, server.CAST_STATE[client]["likely_next_speaker"])

    def test_false_candidates_do_not_pollute_gendered_pronoun_resolution(self):
        client = "gender-state"
        server.remember_entity(client, "Mara")
        server.refine_gender(client, "Mara", "female")
        server.remember_entity(client, "Elias")
        server.refine_gender(client, "Elias", "male")
        self.process(client, "Another impact rattled the roof.")
        self.process(client, "“I said give it to me.”")
        entry = self.process(client, "She stepped away from me.")
        self.assertEqual("Mara", entry["narrative_actor"])
        self.assertNotIn("Another", server.CAST_STATE[client]["recent_entities"])
        self.assertEqual("Mara", server.CAST_STATE[client]["pronouns"]["she"])

    def test_locked_gender_rejects_contradictory_cached_pronouns(self):
        client = "locked-gender"
        server.remember_entity(client, "Mara")
        server.refine_gender(client, "Mara", "female")
        server.remember_entity(client, "Elias")
        server.refine_gender(client, "Elias", "male")
        state = server.CAST_STATE[client]
        state["pronouns"]["she"] = "Elias"
        state["pronouns"]["he"] = "Mara"
        self.assertEqual("Mara", self.process(client, "She stepped away from me.")["narrative_actor"])
        state["pronouns"]["she"] = "Elias"
        self.assertEqual("Mara", self.process(client, "She hesitated.")["narrative_actor"])
        self.assertEqual("Elias", self.process(client, "He frowned.")["narrative_actor"])

    def test_action_dialogue_action_sandwich_keeps_bidirectional_evidence(self):
        client = "sandwich"
        first_action = self.process(client, "Mara crouched beside an overturned planting table.")
        dialogue = self.process(client, "“Here.”")
        second_action = self.process(client, "She held up a brass key.")
        self.assertEqual("narrative-action-cue", first_action["routing_reason"])
        self.assertEqual("Mara", dialogue["cast_speaker"])
        self.assertEqual("Mara", second_action["narrative_actor"])
        self.assertEqual("action-confirms-previous", second_action["routing_reason"])
        self.assertEqual("Mara", server.CAST_STATE[client]["likely_next_speaker"])

    def test_named_preceding_actions_predict_dialogue(self):
        cases = [
            ("Elias ducked.", "“What the hell was that?”"),
            ("Elias raised both hands.", "“Before this becomes...”"),
        ]
        for index, (action, dialogue) in enumerate(cases):
            client = f"preceding-action-{index}"
            self.process(client, action)
            entry = self.process(client, dialogue)
            self.assertEqual("Elias", entry["cast_speaker"])
            self.assertEqual("narrative-action-cue", entry["routing_reason"])

    def test_complete_attributed_turn_does_not_bleed_into_next_quote(self):
        client = "no-adjacent-bleed"
        first = self.process(client, "“Stay here,” Mara said.")
        self.process(client, "Elias ducked.")
        second = self.process(client, "“What was that?”")
        self.assertEqual("Mara", first["cast_speaker"])
        self.assertEqual("Elias", second["cast_speaker"])
        self.assertEqual("narrative-action-cue", second["routing_reason"])

        client = "bare-adjacent"
        self.process(client, "“Stay here,” Mara said.")
        adjacent = self.process(client, "“No.”")
        self.assertNotEqual("adjacent-dialogue-continuation", adjacent["routing_reason"])


if __name__ == "__main__":
    unittest.main()
