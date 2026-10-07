import unittest
import sys
import types
import json
from io import BytesIO
import wave
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient


# Routing tests must not initialize or download the speech model.
fake_kokoro = types.ModuleType("kokoro")
fake_kokoro.KPipeline = lambda *args, **kwargs: None
sys.modules["kokoro"] = fake_kokoro

import server


class RoutingRegressionTests(unittest.TestCase):
    def setUp(self):
        server.LITERARY_STATES.clear()

    def process(self, client, text, narrator_voice=server.DEFAULT_VOICE):
        entry = server.prepare_segment(client, text, narrator_voice)
        server.record_segment(client, entry)
        return entry.public_record()

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
            state = server.LITERARY_STATES[client].scene
            self.assertEqual({}, server.LITERARY_STATES[client].performance.voices)
            self.assertEqual([], list(state.recent_entities))

    def test_real_pre_and_post_quote_attributions_survive(self):
        cases = [
            ("“Traitor,” Mara said.", "Mara"),
            ('Mara said, "Traitor."', "Mara"),
            ("“Traitor,” she said.", "Mara"),
            ('"I\'m sure," I said.', "POV"),
        ]
        for index, (text, expected) in enumerate(cases):
            client = f"valid-attribution-{index}"
            if "she said" in text:
                server.remember_entity(client, "Mara")
                server.refine_gender(client, "Mara", "female")
                server.LITERARY_STATES[client].scene.pronouns["she"] = "Mara"
            entry = self.process(client, text)
            self.assertTrue(entry["has_attribution"])
            self.assertEqual(server.LITERARY_STATES[client].scene.pov_entity_id if expected == "POV" else expected, entry["cast_speaker"])

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
            self.assertEqual([], list(server.LITERARY_STATES[client].scene.participants))
            following = self.process(client, "“Who is there?”")
            self.assertEqual("conservative-dialogue-fallback", following["routing_reason"])
            self.assertEqual([], list(server.LITERARY_STATES[client].scene.recent_entities))

    def test_valid_named_actors_still_work(self):
        for index, text in enumerate((
            "Mara laughed.", "Elias frowned.",
            "Mara pushed through the warped door first.",
        )):
            client = f"valid-actor-{index}"
            entry = self.process(client, text)
            expected = text.split()[0]
            self.assertEqual(expected, entry["narrative_actor"])
            self.assertIn(expected, server.LITERARY_STATES[client].scene.recent_entities)
            self.assertEqual([], list(server.LITERARY_STATES[client].scene.participants))

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
        self.assertNotIn("Another", server.LITERARY_STATES[client].scene.recent_entities)
        self.assertEqual("Mara", server.LITERARY_STATES[client].scene.pronouns["she"])

    def test_locked_gender_rejects_contradictory_cached_pronouns(self):
        client = "locked-gender"
        server.remember_entity(client, "Mara")
        server.refine_gender(client, "Mara", "female")
        server.remember_entity(client, "Elias")
        server.refine_gender(client, "Elias", "male")
        state = server.LITERARY_STATES[client].scene
        state.pronouns["she"] = "Elias"
        state.pronouns["he"] = "Mara"
        self.assertEqual("Mara", self.process(client, "She stepped away from me.")["narrative_actor"])
        state.pronouns["she"] = "Elias"
        self.assertEqual("Mara", self.process(client, "She hesitated.")["narrative_actor"])
        self.assertEqual("Elias", self.process(client, "He frowned.")["narrative_actor"])

    def test_action_dialogue_action_sandwich_keeps_bidirectional_evidence(self):
        client = "sandwich"
        first_action = self.process(client, "Mara crouched beside an overturned planting table.")
        dialogue = self.process(client, "“Here.”")
        second_action = self.process(client, "She held up a brass key.")
        self.assertEqual("narration", first_action["routing_reason"])
        self.assertIsNone(dialogue["cast_speaker"])
        self.assertEqual("Mara", dialogue["decision"]["evidence"][0]["candidate"])
        self.assertEqual("Mara", second_action["narrative_actor"])
        self.assertEqual("narration", second_action["routing_reason"])
        prior = server.LITERARY_STATES[client].context[-2]
        self.assertIsNone(prior.decision.speaker)
        self.assertIsNone(prior.refinement)
        self.assertEqual("Mara", prior.following_evidence[0].candidate)
        self.assertEqual([], list(server.LITERARY_STATES[client].scene.participants))

    def test_named_preceding_actions_are_evidence_only(self):
        cases = [
            ("Elias ducked.", "“What the hell was that?”"),
            ("Elias raised both hands.", "“Before this becomes...”"),
        ]
        for index, (action, dialogue) in enumerate(cases):
            client = f"preceding-action-{index}"
            self.process(client, action)
            entry = self.process(client, dialogue)
            self.assertIsNone(entry["cast_speaker"])
            self.assertEqual("unresolved", entry["decision"]["status"])
            self.assertEqual("Elias", entry["decision"]["evidence"][0]["candidate"])

    def test_complete_attributed_turn_does_not_bleed_into_next_quote(self):
        client = "no-adjacent-bleed"
        first = self.process(client, "“Stay here,” Mara said.")
        self.process(client, "Elias ducked.")
        second = self.process(client, "“What was that?”")
        self.assertEqual("Mara", first["cast_speaker"])
        self.assertIsNone(second["cast_speaker"])
        self.assertEqual("Elias", second["decision"]["evidence"][0]["candidate"])

        client = "bare-adjacent"
        self.process(client, "“Stay here,” Mara said.")
        adjacent = self.process(client, "“No.”")
        self.assertNotEqual("adjacent-dialogue-continuation", adjacent["routing_reason"])


    def test_pov_dialogue_is_distinct_from_prose_and_stable_within_scope(self):
        client = "pov"
        prose = self.process(client, "I crossed the room.")
        self.assertEqual("narration", prose["decision"]["status"])
        self.assertEqual(server.DEFAULT_VOICE, prose["selected_voice"])
        self.assertIsNone(server.LITERARY_STATES[client].scene.pov_entity_id)
        first = self.process(client, '"Wait," I said.')
        pov = first["cast_speaker"]
        self.assertEqual(f"pov:{first['scope_id']}", pov)
        self.assertEqual("resolved", first["decision"]["status"])
        self.assertNotEqual(server.DEFAULT_VOICE, first["selected_voice"])
        self.assertEqual("provisional", first["voice_status"])
        second = self.process(client, '"Come back," I said.')
        self.assertEqual(pov, second["cast_speaker"])
        self.assertEqual(first["selected_voice"], second["selected_voice"])
        self.assertNotIn(pov, server.LITERARY_STATES[client].scene.gender)
        other = self.process("other-scope", '"Wait," I said.')
        self.assertNotEqual(pov, other["cast_speaker"])

    def test_quoted_i_does_not_establish_pov(self):
        for text in ('"I said no."', '"I crossed the room," Mara said.'):
            entry = self.process("quoted-i", text)
            self.assertIsNone(server.LITERARY_STATES["quoted-i"].scene.pov_entity_id)
        self.assertEqual("Mara", entry["cast_speaker"])

    def test_first_person_attribution_prose_stays_narration(self):
        entry = self.process("thought", "I thought about the rain.")
        self.assertEqual("narration", entry["decision"]["status"])
        self.assertIsNone(entry["cast_speaker"])
        self.assertEqual(server.DEFAULT_VOICE, entry["selected_voice"])
        self.assertIsNone(server.LITERARY_STATES["thought"].scene.pov_entity_id)

    def test_pov_quote_continuity_preserves_identity_and_voice(self):
        first = self.process("open-pov", 'I said, "Wait.')
        second = self.process("open-pov", 'Come back."')
        self.assertEqual(first["cast_speaker"], second["cast_speaker"])
        self.assertEqual(first["selected_voice"], second["selected_voice"])
        self.assertEqual("resolved", second["decision"]["status"])
        self.assertEqual("quote-span-continuation", second["routing_reason"])

    def test_unresolved_quote_continuity_does_not_acquire_action_actor(self):
        self.process("open-unknown", "Mara crouched.")
        first = self.process("open-unknown", '"Wait.')
        second = self.process("open-unknown", 'Come back."')
        for entry in (first, second):
            self.assertEqual("unresolved", entry["decision"]["status"])
            self.assertIsNone(entry["cast_speaker"])
        state = server.LITERARY_STATES["open-unknown"]
        self.assertEqual([], list(state.scene.participants))
        self.assertEqual({"Mara"}, set(state.performance.voices))
        for entry in (first, second):
            self.assertEqual("Mara", entry["performance_decision"]["speaker"])
        self.assertIsNone(state.scene.last_speaker)

    def test_unbound_references_are_unresolved_without_placeholder_cast(self):
        for ref in ("she", "he", "they", "we", "you"):
            entry = self.process(ref, f'"Wait," {ref} said.')
            self.assertEqual("unresolved", entry["decision"]["status"])
            self.assertIsNone(entry["cast_speaker"])
            self.assertEqual({}, server.LITERARY_STATES[ref].performance.voices)
            self.assertEqual([], list(server.LITERARY_STATES[ref].scene.participants))

    def test_tentative_pronoun_does_not_seed_confirmed_participants(self):
        self.process("pronoun", "Mara ducked.")
        first = self.process("pronoun", 'She said, "Wait.')
        second = self.process("pronoun", 'Come back."')
        self.assertEqual("Mara", first["cast_speaker"])
        for entry in (first, second):
            self.assertEqual("tentative", entry["decision"]["status"])
        self.assertEqual([], list(server.LITERARY_STATES["pronoun"].scene.participants))

    def test_explicit_attribution_overrides_action_and_consumes_its_adjacency(self):
        self.process("override", "Elias ducked.")
        explicit = self.process("override", '"Wait," Mara said.')
        self.assertEqual("Mara", explicit["cast_speaker"])
        following = self.process("override", '"No."')
        self.assertIsNone(following["cast_speaker"])
        self.assertEqual([], following["decision"]["evidence"])

    def test_action_evidence_expires_across_intervening_prose(self):
        self.process("expiry", "Elias ducked.")
        self.process("expiry", "The rain continued.")
        entry = self.process("expiry", '"Wait."')
        self.assertEqual([], entry["decision"]["evidence"])
        self.assertIsNone(entry["cast_speaker"])

    def test_alternation_is_evidence_without_forcing_a_speaker(self):
        self.process("alternation", '"Wait," Mara said.')
        self.process("alternation", '"No," Elias said.')
        entry = self.process("alternation", '"Come back."')
        self.assertIsNone(entry["cast_speaker"])
        self.assertEqual("two-person-alternation", entry["decision"]["evidence"][0]["kind"])
        self.assertEqual("Mara", entry["decision"]["evidence"][0]["candidate"])
        state = server.LITERARY_STATES["alternation"].scene
        self.assertEqual(["Mara", "Elias"], list(state.participants))
        self.assertIsNone(state.last_speaker)
        following = self.process("alternation", '"Please."')
        self.assertEqual([], following["decision"]["evidence"])

    def test_detached_attribution_refines_without_rewriting_rendered_history(self):
        original = self.process("backward", '"Wait."')
        narration = self.process("backward", "Mara said.")
        prior = server.LITERARY_STATES["backward"].context[-2]
        self.assertEqual(original["decision"], prior.decision.model_dump(mode="json"))
        self.assertEqual(original["selected_voice"], prior.selected_voice)
        self.assertIsNone(prior.decision.speaker)
        self.assertEqual("Mara", prior.refinement.speaker)
        self.assertEqual("resolved", prior.refinement.status)
        self.assertEqual("narration", narration["decision"]["status"])
        self.assertEqual(server.DEFAULT_VOICE, narration["selected_voice"])
        self.assertEqual(["Mara"], list(server.LITERARY_STATES["backward"].scene.participants))

    def test_detached_pov_attribution_and_adjacency_limit(self):
        self.process("backward-pov", '"Wait."')
        self.process("backward-pov", "I said.")
        state = server.LITERARY_STATES["backward-pov"]
        self.assertEqual(state.scene.pov_entity_id, state.context[-2].refinement.speaker)
        self.process("not-adjacent", '"Wait."')
        self.process("not-adjacent", "The rain continued.")
        self.process("not-adjacent", "I said.")
        state = server.LITERARY_STATES["not-adjacent"]
        self.assertIsNone(state.context[0].refinement)
        self.assertIsNone(state.scene.pov_entity_id)

    def test_following_attribution_does_not_replace_explicit_speaker(self):
        self.process("conflict", '"Wait," Mara said.')
        self.process("conflict", "Elias said.")
        prior = server.LITERARY_STATES["conflict"].context[0]
        self.assertEqual("Mara", prior.decision.speaker)
        self.assertIsNone(prior.refinement)

    def test_associated_prose_never_confirms_unresolved_or_tentative_dialogue(self):
        samples = (
            "I thought about the rain.", "Mara thought about the rain.",
            "Mara said nothing.", "Mara did not reply.", "Mara remained silent.",
            "Mara raised her hand.", "I wondered about the rain.",
            "Mara said she would return tomorrow.",
        )
        for status in ("unresolved", "tentative"):
            for index, prose in enumerate(samples):
                client = f"prose-{status}-{index}"
                with self.subTest(status=status, prose=prose):
                    if status == "tentative":
                        self.process(client, "Elias ducked.")
                    self.process(client, '"Wait," he said.' if status == "tentative" else '"Wait."')
                    state = server.LITERARY_STATES[client]
                    prior = state.context[-1]
                    original = prior.public_record()
                    self.process(client, prose)
                    self.assertEqual(status, prior.decision.status)
                    self.assertIsNone(prior.refinement)
                    self.assertEqual(original["decision"], prior.public_record()["decision"])
                    self.assertEqual(original["selected_voice"], prior.selected_voice)
                    self.assertEqual([], list(state.scene.participants))
                    self.assertIsNone(state.scene.pov_entity_id)
                    self.assertEqual(prose, state.context[-1].text)

    def test_bare_speech_tags_can_refine_both_uncertain_outcomes(self):
        for status in ("unresolved", "tentative"):
            for index, tag in enumerate(("Mara said.", "I asked.", "said Mara.")):
                client = f"bare-{status}-{index}"
                with self.subTest(status=status, tag=tag):
                    if status == "tentative":
                        self.process(client, "Elias ducked.")
                    self.process(client, '"Wait," he said.' if status == "tentative" else '"Wait."')
                    state = server.LITERARY_STATES[client]
                    prior = state.context[-1]
                    self.process(client, tag)
                    expected = state.scene.pov_entity_id if tag.startswith("I ") else "Mara"
                    self.assertEqual(status, prior.decision.status)
                    self.assertEqual("resolved", prior.refinement.status)
                    self.assertEqual(expected, prior.refinement.speaker)
                    self.assertEqual([expected], list(state.scene.participants))

    def test_voice_recast_policy_is_preserved(self):
        first = self.process("recast", '"Wait," Mara said.')
        self.assertEqual("provisional", first["voice_status"])
        self.process("recast", "Mara raised her hand.")
        second = self.process("recast", '"Listen," Mara said.')
        self.assertEqual("locked", second["voice_status"])
        self.assertIn(second["selected_voice"], server.FEMALE_VOICE_POOL)
        self.assertNotEqual(server.DEFAULT_VOICE, second["selected_voice"])
        self.assertTrue(server.LITERARY_STATES["recast"].performance.recast_used["Mara"])
        server.refine_gender("recast", "Mara", "male")
        third = self.process("recast", '"Now," Mara said.')
        self.assertEqual(second["selected_voice"], third["selected_voice"])
        self.assertEqual("female", third["cast_gender"])

    def test_tentative_pronoun_retains_gender_binding_and_one_recast(self):
        # Retained legacy policy: tentative identity can still bind gender and
        # consume the one provisional recast, without confirming participation.
        self.process("tentative-recast", "Mara ducked.")
        initial_voice = server.assign_cast_voice("tentative-recast", "Mara", None, server.DEFAULT_VOICE)
        entry = self.process("tentative-recast", '"Wait," she said.')
        state = server.LITERARY_STATES["tentative-recast"]
        self.assertEqual("tentative", entry["decision"]["status"])
        self.assertEqual("Mara", entry["cast_speaker"])
        self.assertEqual("Mara", state.scene.pronouns["she"])
        self.assertEqual("female", state.scene.gender["Mara"])
        self.assertEqual("locked", state.performance.voice_status["Mara"])
        self.assertTrue(state.performance.recast_used["Mara"])
        self.assertNotEqual(initial_voice, entry["selected_voice"])
        self.assertEqual([], list(state.scene.participants))
        self.assertIsNone(state.scene.last_speaker)
        again = self.process("tentative-recast", '"Listen," she said.')
        self.assertEqual("tentative", again["decision"]["status"])
        self.assertEqual(entry["selected_voice"], again["selected_voice"])
        self.assertEqual([], list(state.scene.participants))

    def test_state_round_trip_preserves_decisions_evidence_and_performance(self):
        self.process("roundtrip", "Mara ducked.")
        self.process("roundtrip", '"Wait."')
        self.process("roundtrip", "Mara said.")
        self.process("roundtrip", '"Listen," Mara said.')
        server.refine_gender("roundtrip", "Mara", "female")
        pending = server.LiteraryState.from_json(server.LITERARY_STATES["roundtrip"].to_json())
        self.assertEqual("pending_recast", pending.performance.voice_status["Mara"])
        self.assertNotIn("Mara", pending.performance.voices)
        server.LITERARY_STATES["roundtrip"] = pending
        self.process("roundtrip", '"Now," Mara said.')
        self.process("roundtrip", 'I said, "Come.')
        original = server.LITERARY_STATES["roundtrip"]
        restored = server.LiteraryState.from_json(original.to_json())
        self.assertEqual(original.model_dump(), restored.model_dump())
        self.assertEqual((16, 8, 4), (restored.context.maxlen,
                         restored.scene.recent_entities.maxlen, restored.scene.participants.maxlen))
        server.LITERARY_STATES["roundtrip"] = restored
        entry = self.process("roundtrip", 'Back."')
        self.assertEqual(original.scene.pov_entity_id, entry["cast_speaker"])
        self.assertEqual(original.performance.voices[entry["cast_speaker"]], entry["selected_voice"])
        self.assertTrue(restored.performance.recast_used["Mara"])
        self.assertEqual("locked", restored.performance.voice_status["Mara"])

    def test_round_trip_keeps_unresolved_and_tentative_decisions_and_following_evidence(self):
        self.process("uncertain", '"Wait."')
        self.process("uncertain", "Mara ducked.")
        self.process("uncertain", '"Come back," she said.')
        self.process("uncertain", '"Who is there?')
        original = server.LITERARY_STATES["uncertain"]
        restored = server.LiteraryState.from_json(original.to_json())
        self.assertEqual(original.model_dump(), restored.model_dump())
        self.assertEqual("following-action", restored.context[0].following_evidence[0].kind)
        self.assertEqual("tentative", restored.context[2].decision.status)
        self.assertEqual("unresolved", restored.scene.active_decision.status)
        self.assertEqual([], list(restored.scene.participants))
        server.LITERARY_STATES["uncertain"] = restored
        following = self.process("uncertain", 'Answer me."')
        self.assertIsNone(following["cast_speaker"])
        self.assertEqual("unresolved", following["decision"]["status"])

    def test_serialization_preserves_bounded_memory(self):
        for index in range(30):
            self.process("bounded", f'"Wait," Person{chr(65 + index % 20)} said.')
        original = server.LITERARY_STATES["bounded"]
        restored = server.LiteraryState.from_json(original.to_json())
        self.assertEqual((16, 8, 4), (len(restored.context), len(restored.scene.recent_entities),
                                    len(restored.scene.participants)))
        self.assertEqual(15, restored.context[0].position)

    def test_malformed_and_unsupported_snapshots_are_rejected(self):
        self.process("invalid", '"Wait," I said.')
        state = server.LITERARY_STATES["invalid"]
        base = json.loads(state.to_json())
        mutations = [
            lambda d: d.update(schema_version=2),
            lambda d: d.update(schema_version=True),
            lambda d: d.pop("schema_version"),
            lambda d: d.update(unexpected=True),
            lambda d: d["scene"].update(pov_entity_id="pov:foreign"),
            lambda d: d["scene"].update(pronouns={}),
            lambda d: d["performance"].update(unknown_index=-1),
            lambda d: d["context"][0]["decision"].update(status="unresolved"),
            lambda d: d["context"][0]["decision"].update(speaker="__narrator__"),
            lambda d: d["context"][0].update(scope_id="foreign"),
            lambda d: d["context"][0].update(position=99),
            lambda d: d["context"][0]["decision"]["evidence"][0].update(scope_id="foreign"),
            lambda d: d["scene"].update(participants=["A", "B", "C", "D", "E"]),
        ]
        for mutate in mutations:
            snapshot = json.loads(json.dumps(base))
            mutate(snapshot)
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                server.LiteraryState.from_json(json.dumps(snapshot))
        for malformed in ("{", "null", "[]", "{}"):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                server.LiteraryState.from_json(malformed)

    def test_all_snapshot_identity_locations_reject_invalid_identities(self):
        self.process("identity-fields", 'I said, "Wait.')
        base = json.loads(server.LITERARY_STATES["identity-fields"].to_json())
        base["context"][0]["refinement"] = dict(base["context"][0]["decision"])
        base["context"][0]["following_evidence"] = [dict(base["context"][0]["decision"]["evidence"][0])]
        paths = [
            ("scene", "recent_entities", 0), ("scene", "participants", 0),
            ("scene", "pronouns", "she"), ("scene", "pronouns", "he"),
            ("scene", "last_speaker"), ("scene", "previous_speaker"), ("scene", "pov_entity_id"),
            ("scene", "active_decision", "speaker"),
            ("scene", "active_decision", "evidence", 0, "candidate"),
            ("context", 0, "narrative_actor"), ("context", 0, "classification", "speaker_name"),
            ("context", 0, "decision", "speaker"), ("context", 0, "refinement", "speaker"),
            ("context", 0, "performance_decision", "speaker"),
            ("context", 0, "decision", "evidence", 0, "candidate"),
            ("context", 0, "refinement", "evidence", 0, "candidate"),
            ("context", 0, "following_evidence", 0, "candidate"),
        ]
        base["scene"]["recent_entities"] = ["Mara"]
        maps = [("scene", "gender", "female"), ("performance", "voices", "af_heart"),
                ("performance", "voice_status", "locked"), ("performance", "recast_used", True)]
        for identity in ("__narrator__", "__she__", "__unknown__", "pov:", "pov:foreign",
                         f"pov:{base['scope_id']}:extra", "pov", "", " Mara", "pov :broken"):
            for path in paths:
                snapshot = json.loads(json.dumps(base))
                target = snapshot
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = identity
                with self.subTest(path=path, identity=identity), self.assertRaises(ValueError):
                    server.LiteraryState.from_json(json.dumps(snapshot))
            for owner, field, value in maps:
                snapshot = json.loads(json.dumps(base))
                snapshot[owner][field][identity] = value
                with self.subTest(field=field, identity=identity), self.assertRaises(ValueError):
                    server.LiteraryState.from_json(json.dumps(snapshot))
        # Guard against simply rejecting every populated snapshot in this matrix.
        self.assertEqual(base, json.loads(server.LiteraryState.from_json(json.dumps(base)).to_json()))

    def test_malformed_scope_tokens_are_rejected_even_when_references_agree(self):
        self.process("scope-token", '"Wait," I said.')
        raw = server.LITERARY_STATES["scope-token"].to_json()
        scope = server.LITERARY_STATES["scope-token"].scope_id
        for malformed in ("", " ", "chapter:scene", "chapter/scene", "__reserved__"):
            with self.subTest(scope=malformed), self.assertRaises(ValueError):
                server.LiteraryState.from_json(raw.replace(scope, malformed))

    def test_http_routes_real_assignment_diagnostics_reset_and_client_isolation(self):
        with TestClient(server.app, client=("reader-one", 50000)) as client, \
                patch.object(server, "synthesize", return_value=np.ones(100, dtype=np.float32)) as synth, \
                patch("builtins.print"):
            response = client.post("/v1/audio/speech", json={
                "input": 'I crossed the room.\n"Wait," I said.\n"Who is there?"',
                "voice": "af_heart", "speed": 1.2,
            })
            self.assertEqual(200, response.status_code)
            with wave.open(BytesIO(response.content)) as wav:
                self.assertEqual((1, 24000, 2), (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()))
            entries = client.get("/context").json()["utterances"]
            self.assertEqual(["narration", "resolved", "unresolved"],
                             [e["decision"]["status"] for e in entries])
            self.assertEqual("af_heart", entries[0]["selected_voice"])
            self.assertNotEqual("af_heart", entries[1]["selected_voice"])
            self.assertEqual("af_heart", entries[2]["selected_voice"])
            self.assertEqual([e["selected_voice"] for e in entries], [c.args[1] for c in synth.call_args_list])
            self.assertTrue(all(c.args[2] == 1.2 for c in synth.call_args_list))
            cast = client.get("/cast").json()
            self.assertEqual([entries[1]["cast_speaker"]], cast["participants"])
            self.assertEqual([entries[1]["cast_speaker"]], list(cast["voices"]))
            self.assertIsNone(cast["last_speaker"])
            self.assertIsNone(cast["likely_next_speaker"])
            other = self.process("reader-two", '"Stay," Mara said.')
            self.assertEqual("reset", client.post("/context/reset").json()["status"])
            reset = client.get("/context").json()
            self.assertEqual([], reset["utterances"])
            self.assertFalse(reset["quote_open"])
            self.assertNotEqual(cast["scope_id"], reset["scope_id"])
            self.assertEqual({}, client.get("/cast").json()["voices"])
            self.assertIsNone(client.get("/cast").json()["pov_entity_id"])
            self.assertEqual(other["scope_id"], server.LITERARY_STATES["reader-two"].scope_id)
            client.post("/v1/audio/speech", json={"input": '"Again," I said.'})
            self.assertNotEqual(cast["pov_entity_id"], client.get("/cast").json()["pov_entity_id"])

    def test_performance_matches_resolved_named_and_pov_speakers(self):
        for text in ('"Wait," Mara said.', '"Wait," I said.'):
            entry = self.process("matching", text)
            self.assertEqual(entry["decision"]["speaker"], entry["performance_decision"]["speaker"])
            self.assertEqual("high", entry["performance_decision"]["confidence"])

    def test_performance_guess_is_read_only_for_literary_state(self):
        self.process("guess", "Mara raised her hand.")
        state = server.LITERARY_STATES["guess"]
        state.position += 1
        text = '"Wait.'
        classification = server.classify_utterance("guess", text)
        decision = server.infer_speaker("guess", text, classification)
        before = state.scene.model_dump()
        original = decision.model_dump()
        performance = server.decide_performance(state, decision)
        voice = server.assign_cast_voice("guess", performance.speaker,
                                         state.scene.gender.get(performance.speaker), server.DEFAULT_VOICE)
        self.assertEqual("Mara", performance.speaker)
        self.assertEqual("tentative-preceding-action", performance.reason)
        self.assertEqual("low", performance.confidence)
        self.assertNotEqual(server.DEFAULT_VOICE, voice)
        self.assertEqual(before, state.scene.model_dump())
        self.assertEqual(original, decision.model_dump())
        self.assertEqual("unresolved", state.scene.active_decision.status)
        self.assertIsNone(state.scene.active_decision.speaker)

    def test_later_attribution_can_disagree_with_performance_guess(self):
        self.process("disagree", "Mara ducked.")
        original = self.process("disagree", '"Wait."')
        self.process("disagree", "Elias said.")
        state = server.LITERARY_STATES["disagree"]
        prior = state.context[-2]
        self.assertIsNone(prior.decision.speaker)
        self.assertEqual("unresolved", prior.decision.status)
        self.assertEqual("Mara", prior.performance_decision.speaker)
        self.assertEqual(original["selected_voice"], prior.selected_voice)
        self.assertEqual("Elias", prior.refinement.speaker)
        self.assertEqual(["Elias"], list(state.scene.participants))
        self.assertEqual("Elias", state.scene.last_speaker)
        following = self.process("disagree", '"Again."')
        self.assertIsNone(following["performance_decision"]["speaker"])
        self.assertEqual([], following["decision"]["evidence"])
        restored = server.LiteraryState.from_json(state.to_json())
        self.assertEqual(state.model_dump(), restored.model_dump())

    def test_missing_conflicting_or_cast_only_evidence_uses_narrator(self):
        empty = self.process("empty", '"Wait."')
        self.process("conflicting", '"Yes," Mara said.')
        self.process("conflicting", '"No," Elias said.')
        self.process("conflicting", "Elias ducked.")
        conflict = self.process("conflicting", '"Wait."')
        for entry in (empty, conflict):
            self.assertIsNone(entry["performance_decision"]["speaker"])
            self.assertEqual(server.DEFAULT_VOICE, entry["selected_voice"])
        state = server.LiteraryState()
        state.performance.voices["Mara"] = "af_heart"
        evidence = server.Evidence(kind="preceding-action", scope_id=state.scope_id,
                                   position=1, text="Mara ducked.", candidate="Mara", strength="suggestive")
        decision = server.SpeakerDecision(status="unresolved", reason="test", confidence="low", evidence=(evidence,))
        self.assertIsNone(server.decide_performance(state, decision).speaker)
        self.assertEqual([], list(state.scene.recent_entities))

    def test_alternation_performance_does_not_seed_another_guess(self):
        first = self.process("performance-turns", '"Yes," Mara said.')
        self.process("performance-turns", '"No," Elias said.')
        guess = self.process("performance-turns", '"Please."')
        self.assertEqual("Mara", guess["performance_decision"]["speaker"])
        self.assertEqual(first["selected_voice"], guess["selected_voice"])
        self.assertIsNone(guess["decision"]["speaker"])
        following = self.process("performance-turns", '"Again."')
        self.assertIsNone(following["performance_decision"]["speaker"])
        self.assertEqual([], following["decision"]["evidence"])

    def test_startup_prints_canonical_application_version(self):
        with patch.object(server.app, "version", "test-version"), patch("builtins.print") as output:
            with TestClient(server.app) as client:
                self.assertEqual("test-version", client.get("/health").json()["version"])
            output.assert_any_call("The Margin Proscenium vtest-version", flush=True)

    def test_temporary_ingress_diagnostic_preserves_input_and_filters_metadata(self):
        text = '  First\t phrase.\r\n\nSecond sentence.  '
        with TestClient(server.app) as client, \
                patch.object(server, "synthesize", return_value=np.ones(100, dtype=np.float32)) as synth, \
                patch("builtins.print") as output:
            response = client.post("/v1/audio/speech?token=query-secret", json={
                "input": text, "paragraph_id": "p7", "sequence": 2,
                "api_key": "body-secret", "span_id": {"token": "nested-secret"},
            }, headers={"Authorization": "Bearer header-secret"})
            self.assertEqual(200, response.status_code)
            diagnostic = output.call_args_list[0].args[0]
            lines = diagnostic.splitlines()
            self.assertTrue(lines[0].startswith("=== SPEECH INGRESS REQUEST "))
            self.assertEqual(lines[0].replace("BEGIN", "END"), lines[-1])
            escaped = next(line.removeprefix("input_escaped=") for line in lines
                           if line.startswith("input_escaped="))
            self.assertEqual(text, json.loads(escaped))
            self.assertNotIn(" ", escaped)
            grouping = json.loads(next(line.removeprefix("grouping=") for line in lines
                                       if line.startswith("grouping=")))
            self.assertEqual({"paragraph_id": "p7", "sequence": 2}, grouping)
            self.assertNotIn("secret", diagnostic)
            self.assertEqual(server.segment_text(text), [call.args[0] for call in synth.call_args_list])
            client.post("/v1/audio/speech", json={"input": "Again."})
            diagnostics = [call.args[0] for call in output.call_args_list
                           if call.args[0].startswith("=== SPEECH INGRESS REQUEST ")]
            self.assertEqual(int(diagnostics[0].split()[4]) + 1, int(diagnostics[1].split()[4]))

    def test_synthesis_failure_does_not_append_rendered_context(self):
        cases = {
            "unresolved": '"Wait."', "named": 'Mara said, "Wait.',
            "pov": 'I said, "Wait.', "actor": "Mara raised her hand.",
            "recast": '"Wait," she said.',
        }
        for case, text in cases.items():
            host = f"failure-{case}"
            with self.subTest(case=case), TestClient(server.app, client=(host, 50000)) as client, \
                    patch.object(server, "synthesize", side_effect=RuntimeError("stub failure")):
                if case == "recast":
                    self.process(host, '"Before," Mara said.')
                state = server.LITERARY_STATES[host]
                previous_context = [e.public_record() for e in state.context]
                previous_position, scope = state.position, state.scope_id
                response = client.post("/v1/audio/speech", json={"input": text})
                self.assertEqual(500, response.status_code)
                self.assertEqual(previous_context, client.get("/context").json()["utterances"])
                # Preparation is intentionally not rolled back by a failed synthesis.
                self.assertEqual(previous_position + 1, state.position)
                self.assertEqual(scope, state.scope_id)
                self.assertEqual(case in ("named", "pov"), state.scene.quote_open)
                if case in ("named", "pov"):
                    speaker = "Mara" if case == "named" else f"pov:{scope}"
                    self.assertEqual(speaker, state.scene.active_decision.speaker)
                    self.assertEqual("resolved", state.scene.active_decision.status)
                    self.assertEqual([speaker], list(state.scene.participants))
                    self.assertEqual(speaker, state.scene.last_speaker)
                    self.assertIn(speaker, state.performance.voices)
                    self.assertEqual("provisional", state.performance.voice_status[speaker])
                    self.assertEqual(1, state.performance.unknown_index)
                    self.assertEqual(["Mara"] if case == "named" else [], list(state.scene.recent_entities))
                    self.assertEqual(speaker if case == "pov" else None, state.scene.pov_entity_id)
                elif case in ("actor", "recast"):
                    self.assertEqual(["Mara"], list(state.scene.recent_entities))
                    self.assertEqual("Mara", state.scene.pronouns["she"])
                    self.assertEqual("female", state.scene.gender["Mara"])
                    if case == "recast":
                        self.assertTrue(state.performance.recast_used["Mara"])
                        self.assertEqual("locked", state.performance.voice_status["Mara"])
                        self.assertIn(state.performance.voices["Mara"], server.FEMALE_VOICE_POOL)
                        self.assertEqual(1, state.performance.female_index)
                        self.assertEqual(["Mara"], list(state.scene.participants))
                        self.assertIsNone(state.scene.last_speaker)
                    else:
                        self.assertEqual({}, state.performance.voices)
                        self.assertEqual([], list(state.scene.participants))
                else:
                    self.assertEqual([], list(state.scene.participants))
                    self.assertEqual({}, state.performance.voices)
                    self.assertIsNone(state.scene.pov_entity_id)


if __name__ == "__main__":
    unittest.main()
