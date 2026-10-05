"""Single-policy LCTL game runner with an explicit post-game data handoff.

The policy image chooses every action. Python supplies observations, accounts
for rewards, verifies the replay, and records the integration boundary.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from runtime import MAX_INPUT, PolicyError, load_program, run_program

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "agent.json").read_text(encoding="utf-8"))
AGENT_ID = CONFIG["agent_id"]
MAX_JSON_BYTES = 65536


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value):
    return sha256(canonical(value)).hexdigest()


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in [{low}, {high}]")
    return value


def payoff_matrix(payoffs):
    if not isinstance(payoffs, list) or len(payoffs) != 2:
        raise ValueError("own_payoffs must be a 2-by-2 array")
    for row in payoffs:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError("own_payoffs must be a 2-by-2 array")
        for value in row:
            integer(value, -1000, 1000, "payoff")
    return payoffs


def verified_program():
    expected = CONFIG["policy_sha256"]
    for extension, field in (("lctlc", "source_sha256"), ("brimg", "image_sha256"), ("brir", "brir_sha256")):
        path = ROOT / f"{AGENT_ID}.{extension}"
        if sha256(path.read_bytes()).hexdigest() != expected[field]:
            raise PolicyError(f"Distribution hash mismatch: {path.name}")
    return load_program(ROOT / f"{AGENT_ID}.lctlc")


def choose(observation, program=None):
    if not isinstance(observation, dict):
        raise ValueError("Observation must be an object")
    turn = integer(observation.get("round", 0), 0, 99, "round")
    previous_opponent = integer(observation.get("previous_opponent", 0), 0, 1, "previous_opponent")
    previous_own = integer(observation.get("previous_own", 0), 0, 1, "previous_own")
    payoffs = payoff_matrix(observation["own_payoffs"])
    cumulative = observation.get("counterfactual_totals", [0, 0])
    if not isinstance(cumulative, list) or len(cumulative) != 2:
        raise ValueError("counterfactual_totals must contain exactly two integers")
    for value in cumulative:
        integer(value, -100000, 100000, "counterfactual total")
    registers = [turn, previous_opponent, previous_own,
                 *[value + 1000 for row in payoffs for value in row],
                 *[value + 100000 for value in cumulative]]
    execution = run_program(program or verified_program(), registers)
    action = execution["registers"][15]
    if type(action) is not int or action not in (0, 1):
        raise PolicyError("Policy returned an action outside its two-action contract")
    return {"agent_id": AGENT_ID, "action": action, "vm_steps": execution["steps"],
            "input_sha256": digest(registers), "policy_sha256": CONFIG["policy_sha256"]["image_sha256"]}


def verify_replay(result):
    """Independently check temporal inputs, accounting, and the handoff digest.

    This checker does not execute BRIM or infer whether rewards measure success.
    """
    payoff_matrix(result["own_payoffs"])
    total = 0
    cumulative = [0, 0]
    previous_own = previous_opponent = 0
    rounds = result["rounds"]
    if not 1 <= len(rounds) <= 100:
        raise ValueError("Replay round count outside bounds")
    for index, row in enumerate(rounds):
        action = integer(row["action"], 0, 1, "action")
        opponent = integer(row["opponent_action"], 0, 1, "opponent_action")
        if (row["round"] != index or row["previous_own"] != previous_own
                or row["previous_opponent"] != previous_opponent
                or row["counterfactual_before"] != cumulative):
            raise ValueError("Replay observations are inconsistent")
        registers = [index, previous_opponent, previous_own,
                     *[value + 1000 for values in result["own_payoffs"] for value in values],
                     *[value + 100000 for value in cumulative]]
        if (row["input_sha256"] != digest(registers)
                or row["policy_sha256"] != CONFIG["policy_sha256"]["image_sha256"]):
            raise ValueError("Replay policy or observation identity mismatch")
        reward = result["own_payoffs"][action][opponent]
        if row["reward"] != reward:
            raise ValueError("Replay reward mismatch")
        total += reward
        cumulative = [cumulative[i] + result["own_payoffs"][i][opponent] for i in range(2)]
        previous_own, previous_opponent = action, opponent
    if result["total_payoff"] != total or result["counterfactual_totals"] != cumulative:
        raise ValueError("Replay totals mismatch")
    if result["external_regret"] != max(cumulative) - total:
        raise ValueError("Replay external regret mismatch")
    if result["transcript_sha256"] != digest(rounds):
        raise ValueError("Replay transcript mismatch")
    return {"status": "PASS", "rounds_checked": len(rounds),
            "scope": "Temporal observations, identities and payoff accounting; not prompt completion"}


def play(request):
    if not isinstance(request, dict):
        raise ValueError("Game request must be an object")
    prompt = request.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 24000:
        raise ValueError("prompt must contain 1 to 24000 characters")
    payoffs = payoff_matrix(request["own_payoffs"])
    opponents = request.get("opponent_actions")
    if not isinstance(opponents, list) or not 1 <= len(opponents) <= 100:
        raise ValueError("opponent_actions must contain 1 to 100 moves")
    for opponent in opponents:
        integer(opponent, 0, 1, "opponent action")
    program = verified_program()
    rounds, cumulative = [], [0, 0]
    previous_own = previous_opponent = total = 0
    for index, opponent in enumerate(opponents):
        observation = {"round": index, "own_payoffs": payoffs,
                       "previous_own": previous_own, "previous_opponent": previous_opponent,
                       "counterfactual_totals": list(cumulative)}
        decision = choose(observation, program)
        action = decision["action"]
        reward = payoffs[action][opponent]
        rounds.append({**decision, "round": index, "previous_own": previous_own,
                       "previous_opponent": previous_opponent, "counterfactual_before": list(cumulative),
                       "opponent_action": opponent, "reward": reward})
        total += reward
        cumulative = [cumulative[i] + payoffs[i][opponent] for i in range(2)]
        previous_own, previous_opponent = action, opponent
    result = {"schema": "vivian.lctl-single-agent-game.v1", "agent_id": AGENT_ID,
              "stage": "GAME_COMPLETE", "own_payoffs": payoffs, "rounds": rounds,
              "total_payoff": total, "counterfactual_totals": cumulative,
              "external_regret": max(cumulative) - total,
              "transcript_sha256": digest(rounds),
              "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest()}
    result["verification"] = verify_replay(result)
    draft_count = sum(row["action"] == 0 for row in rounds)
    result["post_game_handoff"] = {
        "schema": "vivian.lctl-single-agent-handoff.v1", "status": "WAITING_FOR_WEIGHTS",
        "agent_id": AGENT_ID, "prompt_sha256": result["prompt_sha256"],
        "transcript_sha256": result["transcript_sha256"],
        "policy_sha256": CONFIG["policy_sha256"]["image_sha256"],
        "coordination_role": "draft" if 2 * draft_count >= len(rounds) else "review",
        "action_role_mapping": {"0": "draft", "1": "review"},
        "required_next_stages": ["TRAINED_DATA", "TASK_EXECUTION", "TASK_VERIFICATION"],
        "task_completed": False,
    }
    return result


def read_json(path):
    with Path(path).open("rb") as handle:
        content = handle.read(MAX_JSON_BYTES + 1)
    if len(content) > MAX_JSON_BYTES:
        raise ValueError("Input JSON exceeds 65536 bytes")
    return json.loads(content)


def main():
    parser = argparse.ArgumentParser(description=CONFIG["display_name"] + " LCTL policy runner")
    parser.add_argument("mode", choices=("choose", "play"))
    parser.add_argument("input", type=Path, help="Path to a JSON observation or game request")
    args = parser.parse_args()
    try:
        request = read_json(args.input)
        result = choose(request) if args.mode == "choose" else play(request)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Input or policy error: {exc}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
