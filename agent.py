"""Bounded LCTL policy execution, verified replay, and a post-game handoff.

Replay verification executes only this distribution's verified pure LCTL policy.
It validates game evidence; it never loads neural weights or completes a task.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import re

from runtime import PolicyError, Program, load_program, run_program

ROOT = Path(__file__).resolve().parent
MAX_JSON_BYTES = 65536
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 10000
GAME_SCHEMA = "vivian.lctl-single-agent-game.v1"
HANDOFF_SCHEMA = "vivian.lctl-single-agent-handoff.v1"
NEXT_STAGES = ("TRAINED_DATA", "TASK_EXECUTION", "TASK_VERIFICATION")
VERIFICATION_SCOPE = "Verified policy execution, observations, accounting and waiting-for-weights handoff; not prompt completion"
GAME_FIELDS = frozenset(("schema", "agent_id", "stage", "own_payoffs", "rounds",
                         "total_payoff", "counterfactual_totals", "external_regret",
                         "transcript_sha256", "prompt_sha256"))
HANDOFF_FIELDS = frozenset(("schema", "status", "agent_id", "prompt_sha256",
                            "transcript_sha256", "policy_sha256", "game_sha256",
                            "coordination_role", "action_role_mapping",
                            "required_next_stages", "task_completed"))
ROW_FIELDS = frozenset(("agent_id", "action", "vm_steps", "input_sha256", "policy_sha256",
                       "round", "previous_own", "previous_opponent", "counterfactual_before",
                       "opponent_action", "reward"))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value):
    return sha256(canonical(value)).hexdigest()


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in [{low}, {high}]")
    return value


def _object(value, allowed, required, label):
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError(f"{label} must be an object with string keys")
    missing, extra = set(required) - value.keys(), value.keys() - set(allowed)
    if missing:
        raise ValueError(f"{label} missing fields: {', '.join(sorted(missing))}")
    if extra:
        raise ValueError(f"{label} has unsupported fields: {', '.join(sorted(extra))}")
    return value


def _hash(value, label):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _prompt(value):
    if type(value) is not str or not value.strip() or len(value) > 24000:
        raise ValueError("prompt must contain 1 to 24000 characters")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("prompt must contain valid Unicode text") from exc
    return value


def _totals(value, label):
    if type(value) is not list or len(value) != 2:
        raise ValueError(f"{label} must contain exactly two integers")
    return [integer(item, -100000, 100000, label) for item in value]


def _cancellation(callback):
    if callback is not None and not callable(callback):
        raise ValueError("check_cancelled must be callable or None")


def payoff_matrix(payoffs):
    if type(payoffs) is not list or len(payoffs) != 2:
        raise ValueError("own_payoffs must be a 2-by-2 array")
    matrix = []
    for row in payoffs:
        if type(row) is not list or len(row) != 2:
            raise ValueError("own_payoffs must be a 2-by-2 array")
        matrix.append([integer(value, -1000, 1000, "payoff") for value in row])
    return matrix


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        try:
            key.encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("JSON object keys must contain valid Unicode text") from exc
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _no_nonfinite(value):
    raise ValueError(f"Non-finite JSON number is not allowed: {value}")


def _json_limits(value):
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ValueError("JSON exceeds nesting or item limits")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Non-finite JSON number is not allowed")
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("JSON strings must contain valid Unicode text") from exc


def read_json(path):
    with Path(path).open("rb") as handle:
        content = handle.read(MAX_JSON_BYTES + 1)
    if len(content) > MAX_JSON_BYTES:
        raise ValueError("Input JSON exceeds 65536 bytes")
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                           parse_constant=_no_nonfinite)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Input must be bounded UTF-8 JSON") from exc
    _json_limits(value)
    return value


CONFIG = read_json(ROOT / "agent.json")
if (type(CONFIG) is not dict or type(CONFIG.get("agent_id")) is not str
        or CONFIG.get("agent_id") not in {
        "player_cooperator", "player_critic", "player_reciprocal", "player_cautious",
        "player_best_reply", "player_learner"}):
    raise PolicyError("Distribution manifest has an invalid agent_id")
AGENT_ID = CONFIG["agent_id"]
_REFERENCE_PROGRAM = None


def _expected_hashes():
    expected = CONFIG.get("policy_sha256")
    fields = ("source_sha256", "image_sha256", "brir_sha256")
    _object(expected, fields, fields, "policy_sha256")
    return {field: _hash(expected[field], field) for field in fields}


def verified_program():
    global _REFERENCE_PROGRAM
    policy = load_program(ROOT / f"{AGENT_ID}.lctlc", ROOT / f"{AGENT_ID}.brimg",
                          ROOT / f"{AGENT_ID}.brir", expected_hashes=_expected_hashes())
    _REFERENCE_PROGRAM = policy
    return policy


def _program(program):
    if program is None:
        return verified_program()
    if not isinstance(program, Program):
        raise PolicyError("Expected a verified Program for this agent")
    pins = _expected_hashes()
    for field, expected in pins.items():
        if getattr(program, field, None) != expected:
            raise PolicyError(f"Cached program {field} does not match this agent")
    reference = _REFERENCE_PROGRAM
    if reference is None or any(getattr(reference, field) != value for field, value in pins.items()):
        reference = verified_program()
    # Compare execution state too: a caller can construct or replace a frozen
    # dataclass while retaining its hash labels. This guards accidental misuse;
    # the Python process itself is trusted and is not a security sandbox.
    if (program.instructions != reference.instructions or program.max_steps != reference.max_steps
            or program.requested_caps != reference.requested_caps):
        raise PolicyError("Cached program execution state differs from the pinned image")
    return program


def choose(observation, program=None, *, check_cancelled=None):
    _cancellation(check_cancelled)
    _object(observation, ("round", "previous_opponent", "previous_own", "own_payoffs",
                          "counterfactual_totals"), ("own_payoffs",), "Observation")
    turn = integer(observation.get("round", 0), 0, 99, "round")
    previous_opponent = integer(observation.get("previous_opponent", 0), 0, 1, "previous_opponent")
    previous_own = integer(observation.get("previous_own", 0), 0, 1, "previous_own")
    payoffs = payoff_matrix(observation["own_payoffs"])
    cumulative = _totals(observation.get("counterfactual_totals", [0, 0]), "counterfactual_totals")
    registers = [turn, previous_opponent, previous_own,
                 *[value + 1000 for row in payoffs for value in row],
                 *[value + 100000 for value in cumulative]]
    policy = _program(program)
    execution = run_program(policy, registers, check_cancelled=check_cancelled)
    action = execution["registers"][15]
    if type(action) is not int or action not in (0, 1):
        raise PolicyError("Policy returned an action outside its two-action contract")
    return {"agent_id": AGENT_ID, "action": action, "vm_steps": execution["steps"],
            "input_sha256": digest(registers), "policy_sha256": policy.image_sha256}


def _game_digest(result):
    return digest({key: result[key] for key in GAME_FIELDS})


def _handoff(result, policy):
    draft_count = sum(row["action"] == 0 for row in result["rounds"])
    return {"schema": HANDOFF_SCHEMA, "status": "WAITING_FOR_WEIGHTS",
            "agent_id": AGENT_ID, "prompt_sha256": result["prompt_sha256"],
            "transcript_sha256": result["transcript_sha256"],
            "policy_sha256": policy.image_sha256, "game_sha256": result["game_sha256"],
            "coordination_role": "draft" if 2 * draft_count >= len(result["rounds"]) else "review",
            "action_role_mapping": {"0": "draft", "1": "review"},
            "required_next_stages": list(NEXT_STAGES), "task_completed": False}


def verify_replay(result, *, expected_prompt=None, check_cancelled=None):
    """Reexecute this verified policy and validate the complete game/handoff.

    Pass the independently retained original prompt as expected_prompt before
    consuming the handoff. Without it, only the prompt hash's format and internal
    consistency can be checked. Hashes are integrity evidence, not signatures.
    No model, external commands, or payload-supplied code are executed.
    """
    _cancellation(check_cancelled)
    required = GAME_FIELDS | {"game_sha256", "post_game_handoff", "handoff_sha256"}
    _object(result, required | {"verification"}, required, "Replay")
    if (result["schema"] != GAME_SCHEMA or result["stage"] != "GAME_COMPLETE"
            or result["agent_id"] != AGENT_ID):
        raise ValueError("Replay schema, stage, or agent identity mismatch")
    prompt_hash = _hash(result["prompt_sha256"], "prompt_sha256")
    if expected_prompt is not None:
        if sha256(_prompt(expected_prompt).encode("utf-8")).hexdigest() != prompt_hash:
            raise ValueError("Replay does not match the expected prompt")
    payoffs = payoff_matrix(result["own_payoffs"])
    rounds = result["rounds"]
    if type(rounds) is not list or not 1 <= len(rounds) <= 100:
        raise ValueError("Replay rounds must contain 1 to 100 objects")
    policy = verified_program()
    total, cumulative = 0, [0, 0]
    previous_own = previous_opponent = 0
    for index, row in enumerate(rounds):
        _object(row, ROW_FIELDS, ROW_FIELDS, "Replay round")
        action = integer(row["action"], 0, 1, "action")
        opponent = integer(row["opponent_action"], 0, 1, "opponent_action")
        integer(row["round"], 0, 99, "round")
        integer(row["previous_own"], 0, 1, "previous_own")
        integer(row["previous_opponent"], 0, 1, "previous_opponent")
        _totals(row["counterfactual_before"], "counterfactual_before")
        integer(row["vm_steps"], 1, policy.max_steps, "vm_steps")
        integer(row["reward"], -1000, 1000, "reward")
        _hash(row["input_sha256"], "input_sha256")
        _hash(row["policy_sha256"], "policy_sha256")
        if (row["round"] != index or row["previous_own"] != previous_own
                or row["previous_opponent"] != previous_opponent
                or row["counterfactual_before"] != cumulative):
            raise ValueError("Replay observations are inconsistent")
        expected = choose({"round": index, "own_payoffs": payoffs,
                           "previous_own": previous_own, "previous_opponent": previous_opponent,
                           "counterfactual_totals": cumulative}, policy,
                          check_cancelled=check_cancelled)
        if any(row[field] != value for field, value in expected.items()):
            raise ValueError("Replay decision or execution evidence does not match the verified policy")
        reward = payoffs[action][opponent]
        if row["reward"] != reward:
            raise ValueError("Replay reward mismatch")
        total += reward
        cumulative = [cumulative[i] + payoffs[i][opponent] for i in range(2)]
        previous_own, previous_opponent = action, opponent
    integer(result["total_payoff"], -100000, 100000, "total_payoff")
    _totals(result["counterfactual_totals"], "counterfactual_totals")
    integer(result["external_regret"], -200000, 200000, "external_regret")
    if result["total_payoff"] != total or result["counterfactual_totals"] != cumulative:
        raise ValueError("Replay totals mismatch")
    if result["external_regret"] != max(cumulative) - total:
        raise ValueError("Replay external regret mismatch")
    if _hash(result["transcript_sha256"], "transcript_sha256") != digest(rounds):
        raise ValueError("Replay transcript mismatch")
    if _hash(result["game_sha256"], "game_sha256") != _game_digest(result):
        raise ValueError("Replay game identity mismatch")
    handoff = _object(result["post_game_handoff"], HANDOFF_FIELDS, HANDOFF_FIELDS, "Post-game handoff")
    if handoff["task_completed"] is not False or handoff != _handoff(result, policy):
        raise ValueError("Post-game handoff must match the verified game and remain waiting for weights")
    if _hash(result["handoff_sha256"], "handoff_sha256") != digest(handoff):
        raise ValueError("Post-game handoff digest mismatch")
    if "verification" in result:
        fields = ("status", "rounds_checked", "scope", "expected_prompt_checked")
        receipt = _object(result["verification"], fields, fields, "Stored verification")
        integer(receipt["rounds_checked"], 1, 100, "rounds_checked")
        if (receipt["status"] != "PASS" or receipt["rounds_checked"] != len(rounds)
                or receipt["scope"] != VERIFICATION_SCOPE
                or type(receipt["expected_prompt_checked"]) is not bool):
            raise ValueError("Stored verification receipt is malformed or inconsistent")
    return {"status": "PASS", "rounds_checked": len(rounds), "scope": VERIFICATION_SCOPE,
            "expected_prompt_checked": expected_prompt is not None}


def play(request, *, check_cancelled=None):
    _cancellation(check_cancelled)
    fields = ("prompt", "own_payoffs", "opponent_actions")
    _object(request, fields, fields, "Game request")
    prompt = _prompt(request["prompt"])
    payoffs = payoff_matrix(request["own_payoffs"])
    opponents = request["opponent_actions"]
    if type(opponents) is not list or not 1 <= len(opponents) <= 100:
        raise ValueError("opponent_actions must contain 1 to 100 moves")
    opponents = [integer(value, 0, 1, "opponent action") for value in opponents]
    if len(canonical({"prompt": prompt, "own_payoffs": payoffs, "opponent_actions": opponents})) > MAX_JSON_BYTES:
        raise ValueError("Game request exceeds 65536 bytes")
    program = verified_program()
    rounds, cumulative = [], [0, 0]
    previous_own = previous_opponent = total = 0
    for index, opponent in enumerate(opponents):
        observation = {"round": index, "own_payoffs": payoffs,
                       "previous_own": previous_own, "previous_opponent": previous_opponent,
                       "counterfactual_totals": list(cumulative)}
        decision = choose(observation, program, check_cancelled=check_cancelled)
        action = decision["action"]
        reward = payoffs[action][opponent]
        rounds.append({**decision, "round": index, "previous_own": previous_own,
                       "previous_opponent": previous_opponent, "counterfactual_before": list(cumulative),
                       "opponent_action": opponent, "reward": reward})
        total += reward
        cumulative = [cumulative[i] + payoffs[i][opponent] for i in range(2)]
        previous_own, previous_opponent = action, opponent
    result = {"schema": GAME_SCHEMA, "agent_id": AGENT_ID,
              "stage": "GAME_COMPLETE", "own_payoffs": payoffs, "rounds": rounds,
              "total_payoff": total, "counterfactual_totals": cumulative,
              "external_regret": max(cumulative) - total,
              "transcript_sha256": digest(rounds),
              "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest()}
    result["game_sha256"] = _game_digest(result)
    result["post_game_handoff"] = _handoff(result, program)
    result["handoff_sha256"] = digest(result["post_game_handoff"])
    result["verification"] = verify_replay(result, expected_prompt=prompt, check_cancelled=check_cancelled)
    return result


def main():
    parser = argparse.ArgumentParser(description=CONFIG["display_name"] + " LCTL policy runner")
    parser.add_argument("mode", choices=("choose", "play"))
    parser.add_argument("input", type=Path, help="Path to a JSON observation or game request")
    args = parser.parse_args()
    try:
        request = read_json(args.input)
        result = choose(request) if args.mode == "choose" else play(request)
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        parser.exit(2, f"Input or policy error: {exc}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
