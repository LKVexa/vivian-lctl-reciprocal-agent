# VIVIAN LCTL Reciprocal Agent

Copies the previous opponent action, using the declared opening observation 0.

This is a standalone, deterministic **game-playing policy** extracted from the
VIVIAN LCTL Agent Team 1.0.0. The included LCTL program chooses each move by writing
action 0 or 1 into register R15. A bounded Python host verifies and executes its
compiled image, supplies game observations, accounts for rewards, and emits a
post-game handoff for future trained BRIM data.

## Run

Requires Python 3.10 or newer. No installation or third-party Python packages are
required for the bundled unsigned policy. From this repository directory:

```console
python agent.py choose observation.json
python agent.py play game.json
python -m unittest -v
```

On Windows, use `py -3` in place of `python` if that is your configured launcher.
Both commands write JSON to standard output. `choose` returns the action and its
execution evidence. `play` runs this policy against the explicit sequence of
opponent moves in the input, checks the resulting replay, and returns
`GAME_COMPLETE` with `WAITING_FOR_WEIGHTS` in the post-game handoff.

The game example uses action 0 = Draft and action 1 = Review. Its engineering
rewards encourage complementary work; they are declared simulation values, not
measurements of prompt correctness. The prompt is hashed to bind the handoff.
Changing its words does not silently alter the payoff model.

## Policy contract

| Register | Input/output |
| --- | --- |
| R0 | Zero-based round, 0–99 |
| R1 | Previous opponent action, 0 or 1; opening observation 0 |
| R2 | Previous own action, 0 or 1; opening observation 0 |
| R3–R6 | Own 2×2 payoff matrix, own-action rows and opponent-action columns, each shifted by +1000 |
| R7–R8 | Cumulative counterfactual payoff for own actions 0 and 1, each shifted by +100000 |
| R15 | Chosen action, 0 or 1 |

The JSON API accepts unshifted integer payoffs from −1000 to 1000 and cumulative
totals from −100000 to 100000; the host applies the shifts. They preserve the
comparisons used by these policies. The replay is limited to 100 rounds. The
current opponent move is withheld from the policy until after its choice; only
the previous move is an observation. The scripted opponent is caller-provided
game data, not another bundled agent.

## BRIM training and execution order

**Game → trained weight data → task execution → verification.**

Training weights and an inference backend are not included. The reserved
handoff records agent/prompt/transcript/policy identities and the majority-move
draft/review role. It explicitly reports that the task is not complete. See
[BRIM integration](BRIM-INTEGRATION.md) for the adapter boundary and host
responsibilities. The small `player_reciprocal.brimg` file is a compiled policy image,
not a language-model weight image.

## BottleRocket compatibility and limits

The policy's `.lctlc`, `.brir` and `.brimg` files are preserved byte-for-byte from
the original team package, compiled with the supplied
`BOTTLE_ROCKET_5.0.1_VM_110K_RC` scaffold. Its source authority remains frozen at
**4.7.0** inside the 5.0.1 maintenance release candidate; the policy's
`version=4.7.0` declaration is intentional. The source format is `LCTLC/1.1`.

The included `brim_ref.py` is BottleRocket's unchanged independent image verifier.
`runtime.py` is a bounded Python adapter for pure CONTROL/ARITH policies. It
admits unsigned 64-bit inputs, caps intermediate values at 256 bits, and bounds
execution to 4096 instructions overall (the included policy declares 256).
Network, memory-service and model execution are outside this adapter's policy
profile. It is not the native signed-image/secure-boot production host.

Hashes in [agent.json](agent.json) check the supplied policy triple. They detect
accidental or uncoordinated changes; an editable local manifest is not a trusted
signature. Recompile changed policy source with the original native BottleRocket
compiler and review/update the hashes together. No compiler or vendor archive is
distributed here.

This repository contains one player's policy and a small test runner. It does not
install VIVIAN, reproduce its GUI, include the full six-agent tournament, infer
utilities from arbitrary prose, establish equilibrium convergence, or complete a
prompt using a model. The game-accounting verifier checks internal consistency,
not whether the declared rewards represent real-world preferences or task success.

## Contents and provenance

- `player_reciprocal.lctlc`, `player_reciprocal.brir`, `player_reciprocal.brimg`: this agent's original source, intermediate representation and image.
- `agent.py`, `agent.json`: standalone host, command line and identity manifest.
- `runtime.py`, `brim_ref.py`: bounded execution adapter and image verifier.
- `observation.json`, `game.json`, `test_agent.py`, `BRIM-INTEGRATION.md`: runnable inputs, checks and integration boundary.
- [PROVENANCE.md](PROVENANCE.md), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md): source origins and retained upstream terms.

The tests compare policy decisions against an independent strategy definition
across 486 observation combinations and check rejected input/tampering, replay
accounting, cancellation, execution bounds and the waiting-for-weights handoff.

## License

The [Product Preview Tester License v1.0](LICENSE) applies to the original project
files. Files identified in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) retain
their supplied upstream terms, including the unchanged BottleRocket verifier and
the license/notice copied alongside it. No training weights are licensed or
distributed by this repository.
