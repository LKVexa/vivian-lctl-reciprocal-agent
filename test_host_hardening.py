"""Regression checks for identity, forged replays, and the model boundary."""
import copy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import agent
from runtime import PolicyCancelled, PolicyError


class HostHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = agent.verified_program()

    def game(self, opponents=None):
        return agent.play({"prompt": "Original retained prompt.", "own_payoffs": [[0, 0], [0, 0]],
                           "opponent_actions": [0] if opponents is None else opponents})

    def rebind(self, result):
        """Recompute every hash; rejection must come from semantic verification."""
        result["transcript_sha256"] = agent.digest(result["rounds"])
        result["game_sha256"] = agent._game_digest(result)
        result["post_game_handoff"] = agent._handoff(result, self.program)
        result["handoff_sha256"] = agent.digest(result["post_game_handoff"])

    def test_policy_pins_pass_to_single_snapshot_loader(self):
        with patch.object(agent, "load_program", return_value=self.program) as loader:
            self.assertIs(agent.verified_program(), self.program)
            self.assertEqual(loader.call_args.kwargs["expected_hashes"], agent.CONFIG["policy_sha256"])
            self.assertEqual(len(loader.call_args.args), 3)

    def test_cached_program_requires_all_three_actual_identities(self):
        for field in ("source_sha256", "image_sha256", "brir_sha256"):
            with self.subTest(field=field):
                wrong = replace(self.program, **{field: "0" * 64})
                with self.assertRaises(PolicyError):
                    agent.choose({"own_payoffs": [[0, 0], [0, 0]]}, wrong)
        receipt = agent.choose({"own_payoffs": [[0, 0], [0, 0]]}, self.program)
        self.assertEqual(receipt["policy_sha256"], self.program.image_sha256)

    def test_forged_action_is_rejected_after_all_hashes_are_recomputed(self):
        result = self.game()
        result["rounds"][0]["action"] ^= 1
        self.rebind(result)
        # All payoffs/totals remain zero; the transcript and handoff are
        # internally consistent but the purported action is not the policy's.
        with self.assertRaisesRegex(ValueError, "verified policy"):
            agent.verify_replay(result)

    def test_cached_program_cannot_replace_execution_state_but_keep_hash_labels(self):
        changed_instructions = (replace(self.program.instructions[0], imm=self.program.instructions[0].imm ^ 1),
                                *self.program.instructions[1:])
        forged = (replace(self.program, instructions=changed_instructions),
                  replace(self.program, max_steps=self.program.max_steps - 1),
                  replace(self.program, requested_caps=self.program.requested_caps ^ 1))
        for program in forged:
            with self.subTest(program=program):
                with self.assertRaisesRegex(PolicyError, "execution state"):
                    agent.choose({"own_payoffs": [[0, 0], [0, 0]]}, program)

    def test_forged_execution_evidence_is_rejected(self):
        baseline = self.game()
        for field, value in (("vm_steps", self.program.max_steps), ("agent_id", "wrong-agent"),
                             ("policy_sha256", "0" * 64), ("input_sha256", "0" * 64)):
            with self.subTest(field=field):
                result = copy.deepcopy(baseline)
                result["rounds"][0][field] = value
                self.rebind(result)
                with self.assertRaises(ValueError):
                    agent.verify_replay(result)

    def test_headers_and_missing_or_extra_fields_are_rejected(self):
        baseline = self.game()
        for field, value in (("schema", "other.schema"), ("agent_id", "wrong-agent"),
                             ("stage", "TASK_COMPLETE"), ("prompt_sha256", "invalid")):
            with self.subTest(field=field):
                result = copy.deepcopy(baseline)
                result[field] = value
                self.rebind(result)
                with self.assertRaises(ValueError):
                    agent.verify_replay(result)
        for missing in ("post_game_handoff", "handoff_sha256", "game_sha256", "rounds"):
            result = copy.deepcopy(baseline)
            del result[missing]
            with self.assertRaises(ValueError):
                agent.verify_replay(result)
        baseline["task_completed"] = True
        with self.assertRaises(ValueError):
            agent.verify_replay(baseline)

    def test_handoff_cannot_claim_task_completion_or_change_binding(self):
        baseline = self.game()
        original_role = baseline["post_game_handoff"]["coordination_role"]
        mutations = (("schema", "other.schema"), ("status", "TASK_COMPLETE"),
                     ("task_completed", True), ("task_completed", 0),
                     ("agent_id", "wrong-agent"), ("prompt_sha256", "0" * 64),
                     ("transcript_sha256", "0" * 64), ("policy_sha256", "0" * 64),
                     ("game_sha256", "0" * 64), ("required_next_stages", []),
                     ("action_role_mapping", {"0": "review", "1": "draft"}),
                     ("coordination_role", "review" if original_role == "draft" else "draft"))
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                result = copy.deepcopy(baseline)
                result["post_game_handoff"][field] = value
                result["handoff_sha256"] = agent.digest(result["post_game_handoff"])
                with self.assertRaises(ValueError):
                    agent.verify_replay(result)

    def test_retained_original_prompt_is_required_to_detect_consistent_substitution(self):
        baseline = self.game()
        self.assertTrue(baseline["verification"]["expected_prompt_checked"])
        self.assertFalse(agent.verify_replay(baseline)["expected_prompt_checked"])
        self.assertTrue(agent.verify_replay(baseline, expected_prompt="Original retained prompt.")
                        ["expected_prompt_checked"])
        modified = copy.deepcopy(baseline)
        modified["prompt_sha256"] = sha256(b"Different prompt.").hexdigest()
        self.rebind(modified)
        # A local hash cannot establish which external prompt was intended.
        self.assertFalse(agent.verify_replay(modified)["expected_prompt_checked"])
        with self.assertRaisesRegex(ValueError, "expected prompt"):
            agent.verify_replay(modified, expected_prompt="Original retained prompt.")

    def test_boolean_and_float_replay_numbers_are_rejected(self):
        baseline = self.game()
        for field in ("round", "previous_own", "previous_opponent", "reward", "vm_steps", "action", "opponent_action"):
            for value in (False, float(baseline["rounds"][0][field])):
                with self.subTest(field=field, value=value):
                    result = copy.deepcopy(baseline)
                    result["rounds"][0][field] = value
                    self.rebind(result)
                    with self.assertRaises(ValueError):
                        agent.verify_replay(result)
        for field in ("total_payoff", "external_regret"):
            result = copy.deepcopy(baseline)
            result[field] = False
            self.rebind(result)
            with self.assertRaises(ValueError):
                agent.verify_replay(result)
        for location in ("before", "after"):
            result = copy.deepcopy(baseline)
            if location == "before":
                result["rounds"][0]["counterfactual_before"] = [False, False]
            else:
                result["counterfactual_totals"] = [False, False]
            self.rebind(result)
            with self.assertRaises(ValueError):
                agent.verify_replay(result)

    def test_digest_tampering_is_rejected(self):
        baseline = self.game()
        for field in ("transcript_sha256", "game_sha256", "handoff_sha256"):
            result = copy.deepcopy(baseline)
            result[field] = "0" * 64
            with self.assertRaises(ValueError):
                agent.verify_replay(result)

    def test_malformed_replay_shapes_fail_with_value_error(self):
        baseline = self.game()
        for value in (None, [], 0, "payload", {}):
            with self.assertRaises(ValueError):
                agent.verify_replay(value)
        for field, value in (("rounds", {}), ("rounds", [None]), ("rounds", []),
                             ("post_game_handoff", []), ("verification", {"status": "PASS"})):
            result = copy.deepcopy(baseline)
            result[field] = value
            with self.assertRaises(ValueError):
                agent.verify_replay(result)

    def test_input_mutations_do_not_rewrite_returned_game(self):
        request = {"prompt": "A", "own_payoffs": [[0, 0], [0, 0]], "opponent_actions": [0]}
        result = agent.play(request)
        request["own_payoffs"][0][0] = 900
        request["opponent_actions"][0] = 1
        self.assertEqual(result["own_payoffs"], [[0, 0], [0, 0]])
        self.assertEqual(agent.verify_replay(result, expected_prompt="A")["status"], "PASS")

    def test_cancellation_reaches_execution_and_replay(self):
        observation = {"own_payoffs": [[0, 0], [0, 0]]}
        request = {"prompt": "A", **observation, "opponent_actions": [0]}
        baseline = agent.play(request)
        for call in (lambda: agent.choose(observation, check_cancelled=lambda: True),
                     lambda: agent.play(request, check_cancelled=lambda: True),
                     lambda: agent.verify_replay(baseline, check_cancelled=lambda: True)):
            with self.assertRaises(PolicyCancelled):
                call()
        steps = baseline["rounds"][0]["vm_steps"]
        calls = 0
        def cancel_at_verification():
            nonlocal calls
            calls += 1
            return calls > steps
        with self.assertRaises(PolicyCancelled):
            agent.play(request, check_cancelled=cancel_at_verification)
        with self.assertRaises(ValueError):
            agent.play(request, check_cancelled=True)

    def test_strict_requests_reject_unknown_fields_and_invalid_unicode(self):
        request = {"prompt": "A", "own_payoffs": [[0, 0], [0, 0]], "opponent_actions": [0]}
        with self.assertRaises(ValueError):
            agent.play({**request, "weights": {"data": "complete"}})
        with self.assertRaises(ValueError):
            agent.choose({"own_payoffs": request["own_payoffs"], "current_opponent": 1})
        with self.assertRaises(ValueError):
            agent.play({**request, "prompt": "\ud800"})
        with self.assertRaises(ValueError):
            agent.play({**request, "prompt": "\U0001f680" * 24000})

    def test_json_rejects_ambiguous_unbounded_or_invalid_payloads(self):
        invalid = [b'{"prompt":"A","prompt":"B"}', b'{"x": NaN}', b'{"x": Infinity}',
                   b'{"x": -Infinity}', b'{"x": 1e999}', b'{"x":"\\ud800"}', b'{"\\ud800":0}', b'\xff',
                   b'[' * 40 + b'0' + b']' * 40,
                   b'[' + b','.join([b'0'] * 10001) + b']', b' ' * (agent.MAX_JSON_BYTES + 1)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for content in invalid:
                with self.subTest(content=content[:60]):
                    path.write_bytes(content)
                    with self.assertRaises(ValueError):
                        agent.read_json(path)
            path.write_bytes(b'{"valid": [0, 1]}')
            self.assertEqual(agent.read_json(path), {"valid": [0, 1]})

    def test_cli_invalid_json_has_clear_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_bytes(b'{"prompt":"A","prompt":"B"}')
            completed = subprocess.run([sys.executable, "-B", str(agent.ROOT / "agent.py"), "play", str(path)],
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("Duplicate JSON object key", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_complete_handoff_survives_json_round_trip(self):
        result = self.game([0, 1] * 50)
        recovered = json.loads(json.dumps(result))
        receipt = agent.verify_replay(recovered, expected_prompt="Original retained prompt.")
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["rounds_checked"], 100)
        self.assertFalse(recovered["post_game_handoff"]["task_completed"])


if __name__ == "__main__":
    unittest.main()
