# Changelog

## 1.1.1 — 2026-10-04

Hardens the standalone **Reciprocal** agent's host and replay boundary while
preserving the agent's policy source, intermediate representation and image.

- Reexecute the pinned LCTL policy for each replay move; validate exact action,
  execution steps, agent identity, observations and integer payoff accounting.
- Validate complete game and handoff schemas. Reject altered completion claims,
  roles, required stages and mismatched prompt/policy/transcript identities.
- Add whole-game and handoff binding hashes. Require regenerated receipts when
  older results lack these fields. Expose `expected_prompt_checked` and require
  the retained original prompt when consuming a handoff for model integration.
- Verify manifest pins against the same bounded source/image/IR snapshots that
  are parsed and executed. Require all three policy components in this runner.
- Reject cached programs whose identities, instructions, step limit or
  capabilities differ from the pinned reference policy.
- Reject duplicate JSON keys, nonfinite numbers, invalid Unicode, excessive
  nesting/item counts, unknown fields and bool/float substitutes for integers.
- Propagate cancellation through game execution and replay; copy request data so
  later caller mutations do not rewrite an emitted game.
- Add host/runtime regression suites, for 34 tests per standalone repository.
- Clarify the trusted Python-host boundary, hash limitations and future model
  responsibilities. Keep the full shared-team implementation separate.

The bespoke Product Preview Tester License, third-party notices, upstream
BottleRocket verifier and policy byte hashes are unchanged. The package includes
no neural weights or inference backend and still stops at WAITING_FOR_WEIGHTS.
