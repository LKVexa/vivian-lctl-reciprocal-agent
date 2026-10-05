import copy
from hashlib import sha256
import itertools
import json
from pathlib import Path
import tempfile
import unittest

import agent
from runtime import MAX_INPUT, PolicyCancelled, PolicyError, load_program, run_program


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = agent.verified_program()

    def test_policy_matches_independent_strategy(self):
        # Exercise negative/equal/positive rewards, both previous opponent moves,
        # and counterfactual ties and strict preferences independently of the VM.
        for entries in itertools.product((-1000, 0, 1000), repeat=4):
            payoffs = [list(entries[:2]), list(entries[2:])]
            for previous_opponent in (0, 1):
                for cumulative in ([-5, 8], [0, 0], [9, -9]):
                    observation = {"own_payoffs": payoffs, "previous_opponent": previous_opponent,
                                   "counterfactual_totals": cumulative}
                    expected = previous_opponent
                    self.assertEqual(agent.choose(observation, self.program)["action"], expected)

    def test_play_handoff_and_simultaneous_observations(self):
        request = {"prompt": "Prepare a draft and a review.", "own_payoffs": [[2, 5], [4, 1]],
                   "opponent_actions": [1, 0, 1, 1, 0]}
        result = agent.play(request)
        self.assertEqual(result["stage"], "GAME_COMPLETE")
        self.assertEqual(result["post_game_handoff"]["status"], "WAITING_FOR_WEIGHTS")
        self.assertFalse(result["post_game_handoff"]["task_completed"])
        self.assertEqual([r["previous_opponent"] for r in result["rounds"]], [0, 1, 0, 1, 1])
        self.assertEqual(result["verification"]["status"], "PASS")
        self.assertEqual(result["prompt_sha256"], sha256(request["prompt"].encode()).hexdigest())

    def test_rewards_do_not_depend_on_prompt_text(self):
        request = {"prompt": "First text", "own_payoffs": [[2, 5], [4, 1]], "opponent_actions": [0, 1]}
        first = agent.play(request)
        request["prompt"] = "Different text"
        second = agent.play(request)
        self.assertEqual(first["transcript_sha256"], second["transcript_sha256"])
        self.assertNotEqual(first["prompt_sha256"], second["prompt_sha256"])

    def test_independent_replay_rejects_corrupt_accounting(self):
        baseline = agent.play({"prompt": "A", "own_payoffs": [[-5, 2], [3, -1]], "opponent_actions": [1, 0]})
        for key, value in (("reward", 123), ("previous_opponent", 1), ("input_sha256", "bad")):
            modified = copy.deepcopy(baseline)
            modified["rounds"][0][key] = value
            modified["transcript_sha256"] = agent.digest(modified["rounds"])
            with self.assertRaises(ValueError):
                agent.verify_replay(modified)

    def test_invalid_inputs_are_rejected(self):
        for key, value in (("previous_opponent", 2), ("previous_own", True), ("round", 100),
                           ("own_payoffs", [[0, 0], [0, -1001]]),
                           ("counterfactual_totals", [0, 100001])):
            observation = {"own_payoffs": [[2, 5], [4, 1]], key: value}
            with self.assertRaises(ValueError):
                agent.choose(observation, self.program)
        with self.assertRaises(ValueError):
            agent.play({"prompt": "A", "own_payoffs": [[0, 0], [0, 0]], "opponent_actions": [0] * 101})

    def test_runtime_input_and_cancellation_bounds(self):
        for values in ([MAX_INPUT + 1], [-1], [True], {16: 1}):
            with self.assertRaises(PolicyError):
                run_program(self.program, values)
        with self.assertRaises(PolicyCancelled):
            run_program(self.program, check_cancelled=lambda: True)

    def test_source_and_image_tampering_are_rejected(self):
        source = Path(self.program.source_path)
        image = Path(self.program.image_path)
        with tempfile.TemporaryDirectory() as directory:
            changed_source = Path(directory) / source.name
            changed_source.write_bytes(source.read_bytes() + b"# altered\n")
            with self.assertRaises(PolicyError):
                load_program(changed_source, image)
            broken_image = Path(directory) / image.name
            broken_image.write_bytes(image.read_bytes()[:-1])
            with self.assertRaises(PolicyError):
                load_program(source, broken_image)

    def test_brir_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            brir = Path(directory) / "changed.brir"
            brir.write_bytes(b"changed")
            with self.assertRaises(PolicyError):
                load_program(self.program.source_path, self.program.image_path, brir)

    def test_maximum_game_is_bounded(self):
        result = agent.play({"prompt": "A", "own_payoffs": [[-1000, 1000], [1000, -1000]],
                             "opponent_actions": [0, 1] * 50})
        self.assertEqual(len(result["rounds"]), 100)
        self.assertTrue(all(row["vm_steps"] <= 256 for row in result["rounds"]))


if __name__ == "__main__":
    unittest.main()
