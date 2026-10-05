# BRIM integration boundary

The required order is **game → trained weight data → task execution → verification**.

This standalone package completes only this agent's bounded game stage. `play`
returns a `post_game_handoff` with `WAITING_FOR_WEIGHTS`, `task_completed: false`,
the agent and policy identities, the prompt hash, the game transcript hash, and
the role selected from the agent's majority move. Ties select Draft. The mapping
is explicitly action 0 = draft and action 1 = review.

The generated transcript contains the observations, actual policy actions and
payoffs. The host must retain the original prompt; the handoff includes its
SHA-256 identity and does not recover the text from that hash.

Integrating the future model is host work, after game verification succeeds:

1. Bind the model/data response to the handoff's agent, prompt, transcript and
   policy identities. Record the model/image version and provenance alongside it.
2. Obtain the requested task data from the trained BRIM model through an explicit
   adapter. Validate response shape and resource limits. Treat its content as
   data, never as executable LCTL, Python, shell commands or authorization.
3. Supply those validated data and the retained prompt to the selected task role.
4. Run a task-specific acceptance check before recording task completion.

No training files, trained weights, model loader or inference backend is bundled.
The integration schema above is this standalone package's boundary, not a claim
of drop-in compatibility with a future model image format. It leaves the current
training process untouched. Policy `.brimg` files in the repository root are small compiled
game programs; they are not language-model weights.

For the complete VIVIAN application, merge/adapt this policy through VIVIAN's team
and model adapters. This repository does not replace that application's full team
scheduler, durable jobs, GUI, inference backend or task verifier.
