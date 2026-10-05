# VIVIAN LCTL Reciprocal Agent

**Version 1.1.1 · Product preview**

Copies the opponent's previous observed action, using action 0 as its opening input. It does not observe the current simultaneous opponent move or guarantee cooperation.

This repository contains one deterministic **game-playing LCTL policy**, its
bounded Python host, and tests. The policy writes its move to register R15.
The host supplies observations and accounts for rewards, then independently
reexecutes the pinned policy to verify the complete game and post-game handoff.

## Run

Requires Python 3.10 or newer. No third-party Python packages are required.
From this repository directory:

```console
python agent.py choose observation.json
python agent.py play game.json
python -m unittest -v
```

On Windows, use `py -3` if that is your configured launcher. Both runner commands
write JSON to standard output. `choose` returns one action and execution evidence.
`play` uses the supplied opponent sequence and returns `GAME_COMPLETE` with a
`WAITING_FOR_WEIGHTS` handoff and `task_completed: false`.

The example maps action 0 to Draft and action 1 to Review. Its rewards are declared
simulation values, not measurements of prompt correctness. Changing prompt words
changes the prompt and game binding hashes, without silently altering the payoffs.
The transcript hash can remain identical for different prompts.

## Policy contract

| Register | Input/output |
| --- | --- |
| R0 | Zero-based round, 0–99 |
| R1 | Previous opponent action, 0 or 1; opening observation 0 |
| R2 | Previous own action, 0 or 1; opening observation 0 |
| R3–R6 | Own 2×2 payoff matrix, own-action rows and opponent-action columns, shifted by +1000 |
| R7–R8 | Cumulative counterfactual payoff for actions 0 and 1, shifted by +100000 |
| R15 | Chosen action, 0 or 1 |

JSON payoffs are integers from −1000 to 1000 and cumulative totals are integers
from −100000 to 100000. Booleans and floating-point substitutes are rejected.
The shifts preserve the policy's comparisons. Games contain 1–100 rounds. The
current opponent move is withheld until after the policy chooses; the opponent
sequence is caller-provided game data, not another bundled agent.

Requests and replay objects use strict field sets. Unknown fields, duplicate
JSON keys, nonfinite numbers and invalid Unicode are rejected. Input files are
limited to 65,536 bytes, nesting depth 32 and 10,000 values. A prompt must contain
1–24,000 characters and its complete request must also fit the byte limit.

## Verified replay and BRIM boundary

**Game → trained weight data → task execution → verification.**

`verify_replay` checks schema and agent identity, temporal observations, exact
actions and VM step counts by reexecuting the pinned policy, integer accounting,
all binding hashes, and the complete waiting-for-weights handoff. It checks only
this bounded game and integration boundary; it does not verify prompt completion.

The result contains a transcript hash, a `game_sha256` covering the prompt identity
and complete game evidence, and a `handoff_sha256` covering the handoff. The handoff
also carries the game hash, policy identity and majority-move draft/review role.
Role ties select Draft. Before consuming a saved handoff, retain the original
prompt outside the result and verify against it:

```python
from agent import verify_replay

receipt = verify_replay(saved_result, expected_prompt=retained_original_prompt)
assert receipt["expected_prompt_checked"] is True
```

Without `expected_prompt`, verification can check prompt-hash format and internal
consistency only; the fresh receipt reports `expected_prompt_checked: false`.
Do not rely on a stored `verification` object as proof of a fresh check.
Older replay receipts without the new binding fields must be regenerated using
this version's `play`; the example request and observation formats still work.

The library functions `choose`, `play` and `verify_replay` accept an optional
`check_cancelled` callback. Cancellation propagates through policy execution and
replay verification; cancellation does not return a completed game result.

No weights, model loader, inference backend, training files or full team scheduler
are included. See [BRIM integration](BRIM-INTEGRATION.md) for the future host's
responsibilities. `player_reciprocal.brimg` is a small compiled game policy, not a
language-model weight image. This repository leaves training untouched.

## BottleRocket compatibility and limits

The `.lctlc`, `.brir` and `.brimg` policy files are unchanged from the original
VIVIAN LCTL Agent Team 1.0.0 and were compiled with the supplied
`BOTTLE_ROCKET_5.0.1_VM_110K_RC`. Its source authority is frozen at **4.7.0**, so the
policy's `version=4.7.0` declaration is intentional. Source format is `LCTLC/1.1`.

`brim_ref.py` is the unchanged BottleRocket image verifier. `runtime.py` is a
hardened Python adapter for pure CONTROL/ARITH policies. It reads each policy
component once with a size bound and checks manifest hashes against those same
snapshots before parsing. All three components are required by this runner.
Cached programs must match both the source/image/IR identities and the execution
state of a pinned reference loaded by the host.

The adapter admits unsigned 64-bit inputs, limits intermediate values to 256 bits,
and caps execution at 4096 steps overall; this policy declares 256. It does not
provide the native signed-image/secure-boot production host. Hashes detect mismatch
against the local manifest; an editable manifest is not publisher authentication.
The local Python process and its code are trusted. Its APIs are not a sandbox
against someone able to modify or execute arbitrary Python in that process.

This single-agent repository does not install VIVIAN, include its GUI or full
six-agent tournament, infer utility from arbitrary prose, promise equilibrium
convergence, or complete a prompt using a model. The shared VIVIAN team is
maintained separately; these files are not a complete team installation.

## Contents and validation

- `player_reciprocal.lctlc`, `player_reciprocal.brir`, `player_reciprocal.brimg`: unchanged agent policy triple.
- `agent.py`, `agent.json`: standalone runner and versioned identity manifest.
- `runtime.py`, `brim_ref.py`: bounded execution adapter and upstream verifier.
- `observation.json`, `game.json`: runnable inputs.
- `test_agent.py`, `test_host_hardening.py`, `test_runtime_hardening.py`: 34 checks.
- [Provenance](PROVENANCE.md), [upstream notices](THIRD_PARTY_NOTICES.md), [changelog](CHANGELOG.md).

The tests include an independent strategy comparison across 486 observation
combinations, input and image tampering, forged replay and completion claims,
single-snapshot file admission, cancellation, maximum games and JSON boundaries.

## License

The [Product Preview Tester License v1.0](LICENSE) applies to original project
files. [Third-party files](THIRD_PARTY_NOTICES.md) retain their upstream licenses,
including the unchanged BottleRocket verifier and the accompanying license and
notice. No neural weights are licensed or distributed here.
