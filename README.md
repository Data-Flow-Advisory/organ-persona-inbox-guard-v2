# organ-persona-inbox-guard-v2

A pure decision **organ** for the Data-Flow-Advisory orchestrator: decides whether
an envelope-driven persona's standing-directive *heartbeat* should be **skipped**
because the persona awaits incoming envelopes and has nothing to respond to.

> **Why v2?** This is a clean re-extraction (a *fork*, not an in-place fix) of the
> RED [`organ-persona-inbox-guard`](https://github.com/Data-Flow-Advisory/organ-persona-inbox-guard),
> per the orchestrator principle *"a failed organ can be forked."* The v1 organ's
> conformance was failing on a test/rationale casing mismatch and it omitted the
> CONTRACT-required CLI entrypoint (`stdin` / `$ORGAN_INPUT` → `stdout`). v2 fixes
> both: self-consistent tests, a contract-compliant entrypoint, and a conformance
> Action that shadow-runs the samples.

## What it decides

Some personas exist only to answer envelopes addressed to them — Morgan answers
`research_request` envelopes, a future Matt-Review answers `review_request`, etc.
Their `standing_directive` is the protocol contract, not a self-initiated work
loop. Without a guard the seeder fires a `claude -p` on the standing directive
every interval tick **even when the inbox is empty**, burning a dev-tier Anthropic
invocation plus audit churn (discovery-engine items 11574 / 12111 / 12120).

A heartbeat is **skipped** (`output: true`) iff **all** of:

1. the persona is **envelope-driven** (resolves a non-empty set of inbox
   `action_types`), **and**
2. there is **no** pending/claimed delegated work item, **and**
3. there is **no** pending/shown matching inbox widget action.

Any check failing → `output: false` (run the heartbeat). A persona that is not
envelope-driven always runs.

## Contract

Implements the [orchestrator organ contract](https://github.com/Data-Flow-Advisory/orchestrator/blob/main/CONTRACT.md):
a **pure decider** — reads facts, returns advice, never acts.

```python
def decide(state: dict, context: dict | None = None) -> dict
```

- **Pure**: no network, no DB, no side effects. The two IO facts
  (`has_delegated_work`, `has_pending_action`) are *handed in* via `state` — the
  organ never fetches them. (In discovery-engine, the spine performs those
  fail-open DB reads; this organ is the decision core only.)
- **Deterministic**: same input → same output.
- **Fail-safe**: malformed/empty `state` → the conservative verdict
  (`output: false`, run the heartbeat) at reduced confidence — never a
  confident-wrong "skip".
- **Stdlib-only.**

### State schema

```json
{
  "persona_config": { "role_id": "researcher" },
  "has_delegated_work": false,
  "has_pending_action": false
}
```

`persona_config` resolution for envelope action_types (first non-empty wins):

1. `persona_config.inbox_envelope_action_types` — explicit per-persona override.
2. `role_id` mapping (`researcher` → `research_request`).
3. `[]` — not envelope-driven.

### Output

```json
{
  "output": true,
  "rationale": "Envelope-driven persona with no pending delegated work and no pending inbox action (action_types=['research_request']); skip heartbeat.",
  "self_metric": { "confidence": 1.0, "action_types_count": 1 }
}
```

## Run it

```bash
# As a CLI (the orchestrator runner shape): JSON on stdin or via $ORGAN_INPUT.
echo '{"state": {"persona_config": {"role_id": "researcher"}, "has_delegated_work": false, "has_pending_action": false}}' | python3 organ.py
ORGAN_INPUT=samples/envelope_driven_empty_inbox.json python3 organ.py

# As a library.
python3 -c "from organ import decide; print(decide({'persona_config': {'role_id': 'researcher'}}))"
```

## Samples

| File | Verdict |
|------|---------|
| `samples/envelope_driven_empty_inbox.json` | `true` — skip (empty inbox) |
| `samples/envelope_driven_pending_work.json` | `false` — run (delegated work waiting) |
| `samples/not_envelope_driven.json` | `false` — run (not envelope-driven) |

## Tests & conformance

```bash
python -m pytest test_organ.py -v
```

CI (`.github/workflows/conformance.yml`) runs the pytest suite, verifies the
`decide` contract shape + determinism + fail-safe, and shadow-runs every sample
through the CLI entrypoint into the job summary.

## Source

Re-extracted from discovery-engine `app/services/persona_inbox_guard.py`
(`inbox_action_types_for_persona` + the `should_skip_envelope_driven_heartbeat`
composition). The DB predicates are projected into the two `state` booleans.
