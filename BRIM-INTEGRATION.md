# BRIM integration boundary — Reciprocal

The required order is **game → trained weight data → task execution → verification**.

This repository provides the `player_reciprocal` policy and its bounded game runner.
It completes only this agent's game stage. Copies the opponent's previous observed action, using action 0 as its opening input. It does not observe the current simultaneous opponent move or guarantee cooperation.

## Consume a verified game

`play` returns `GAME_COMPLETE` and a `post_game_handoff` with:

- `status: "WAITING_FOR_WEIGHTS"` and `task_completed: false`;
- the agent, prompt, policy and transcript identities;
- `game_sha256`, binding the prompt identity, declared payoffs, rounds and totals;
- a majority-move `coordination_role`, with Draft selected on ties;
- action-role mapping `0 = draft`, `1 = review`;
- remaining stages `TRAINED_DATA`, `TASK_EXECUTION`, `TASK_VERIFICATION`.

The outer result also includes `handoff_sha256`, covering the full handoff.
The prompt text is not recoverable from its hash: the host must retain it.
Before accepting a saved game for model integration, call:

```python
receipt = verify_replay(saved_result, expected_prompt=retained_original_prompt)
assert receipt["expected_prompt_checked"] is True
```

Import `verify_replay` from `agent`. It loads this repository's pinned policy and
reexecutes each move, checking exact action and step evidence, temporal inputs,
accounting, schemas, binding hashes and the complete waiting state. It does not
execute payload-supplied code, neural inference or external commands.

Without the independently retained prompt, only hash syntax and internal
consistency can be established. A fresh receipt then reports
`expected_prompt_checked: false`, regardless of what a stored receipt claims.
Local hashes are integrity checks, not signatures or proof of publisher identity.
Keep the host code and local manifest in a trusted environment.

Receipts from older versions without `game_sha256`, `handoff_sha256` and the updated
verification fields are rejected. Regenerate them from retained game inputs with
this version's `play`; do not invent missing evidence or set verification to PASS.

## Future model adapter responsibilities

Only after the fresh replay check succeeds:

1. Bind model/data responses to the handoff's agent, prompt, policy, transcript,
   game and handoff identities. Record the actual model/image version and the
   source of supplied data. Match against retained expected identities.
2. Obtain task data through an explicit adapter for the eventual trained BRIM
   model. Validate response shape, size and provenance. Treat model content as
   data, not executable LCTL, Python, shell commands or user authorization.
3. Supply validated data and the retained original prompt to the assigned role.
4. Run an independent, task-specific acceptance check before recording actual
   task completion. A game score, replay PASS or model response alone is not it.

The standalone package contains no implementation of those model, task-execution
or acceptance stages, and cannot validate their future payloads yet. Do not change
its waiting state to claim completion merely because the game verified.

No training data, trained weights, tokenizer, inference loader or model backend
is bundled. `player_reciprocal.brimg` is a compiled game policy, not a trained neural
weight image. The eventual model format and adapter contract remain separate.
The training process is untouched by this package.

The VIVIAN shared team is maintained and hardened separately. Integrating this
individual policy into that application requires its team/model adapters; this
repository is not the full tournament, durable-job host, GUI or task verifier.
