#!/usr/bin/env python3
"""persona_inbox_guard organ — a pure envelope-driven heartbeat-skip decider.

Implements the organ contract (orchestrator CONTRACT.md): reads
``{state, context}`` JSON on stdin (or the file named by ``$ORGAN_INPUT``),
writes ``{output, rationale, self_metric}`` JSON on stdout. Pure: no network,
no DB, no side effects, deterministic. Fails safe to the **conservative**
verdict — "run the heartbeat" — on malformed/empty state.

WHY THIS ORGAN EXISTS
---------------------
Some personas exist solely to respond to envelopes addressed to them — Morgan
answers ``research_request`` envelopes, a future Matt-Review answers
``review_request``, etc. Their ``standing_directive`` is the protocol contract,
not a self-initiated work loop. Without a guard, the seeder fires a ``claude -p``
on the standing directive every interval tick regardless of whether any envelope
is waiting — each empty-inbox heartbeat costs one dev-tier Anthropic invocation
plus audit churn (items 11574 / 12111 / 12120 in discovery-engine).

This organ is the **pure decision core** re-extracted from discovery-engine
``app/services/persona_inbox_guard.py``. The original module composes a pure
predicate (``inbox_action_types_for_persona``) with two fail-open DB reads
(``has_pending_delegated_work_item`` / ``has_pending_inbox_widget_action``). Per
the contract, the organ is *handed* the two IO facts in ``state`` as booleans —
it never fetches them. The spine (the seeder) performs the reads and the effect
(skip vs seed).

DECISION
--------
A persona's standing-directive heartbeat is SKIPPED iff ALL hold:

1. The persona is envelope-driven (resolves a non-empty set of inbox
   action_types), AND
2. There is no pending/claimed delegated work item, AND
3. There is no pending/shown matching inbox widget action.

Any check failing → output ``False`` (run the heartbeat). A persona that is not
envelope-driven always runs the heartbeat (the seeder's normal behaviour).

STATE SCHEMA
------------
    {
      "persona_config":      dict | None,   # persona's config JSON
      "has_delegated_work":  bool,          # pending/claimed delegated item?
      "has_pending_action":  bool           # pending/shown inbox action?
    }

OUTPUT
------
    {
      "output": bool,                       # True = skip heartbeat, False = run
      "rationale": str,                     # why, derivable from state alone
      "self_metric": {"confidence": float, ...}
    }
"""
from __future__ import annotations

import json
import os
import sys


# role_id -> action_types that count as an inbox arrival for that role.
# Bare-kind convention (``research_request``, not ``research_request.v1``),
# matching PendingWidgetAction.action_type in discovery-engine.
# Adding a new envelope-driven role is one line here.
_ROLE_ID_INBOX_ACTION_TYPES: dict[str, tuple[str, ...]] = {
    "researcher": ("research_request",),
}


def inbox_action_types_for_persona(persona_config: dict | None) -> list[str]:
    """Return the action_types that count as inbox arrivals for an
    envelope-driven persona, or ``[]`` if the persona is not envelope-driven.

    Resolution order — first non-empty wins:

    1. ``persona_config["inbox_envelope_action_types"]`` — explicit per-persona
       override (list of non-empty strings).
    2. ``role_id``-derived mapping (researcher -> research_request).
    3. ``[]`` (default — heartbeat-OK).

    Pure, no IO. Defensive against missing/malformed configs.
    """
    if not isinstance(persona_config, dict):
        return []
    explicit = persona_config.get("inbox_envelope_action_types")
    if isinstance(explicit, list):
        return [k for k in explicit if isinstance(k, str) and k]
    role_id = persona_config.get("role_id")
    if not isinstance(role_id, str):
        return []
    return list(_ROLE_ID_INBOX_ACTION_TYPES.get(role_id, ()))


def decide(state: dict, context: dict | None = None) -> dict:
    """Decide whether to skip a persona's standing-directive heartbeat.

    See module docstring for the state schema and decision logic. Pure,
    deterministic, fail-safe: malformed/empty state yields the conservative
    "run the heartbeat" verdict (``output=False``) at reduced confidence —
    never a confident-wrong "skip".
    """
    context = context or {}

    # Fail-safe: a non-dict state cannot prove a persona is envelope-driven.
    # Conservative verdict is "run the heartbeat" so we never silently starve a
    # persona of its scheduled work on bad input.
    if not isinstance(state, dict):
        return {
            "output": False,
            "rationale": "Malformed state (not an object); conservatively run heartbeat.",
            "self_metric": {"confidence": 0.5, "action_types_count": 0},
        }

    persona_config = state.get("persona_config")
    has_delegated_work = bool(state.get("has_delegated_work", False))
    has_pending_action = bool(state.get("has_pending_action", False))

    action_types = inbox_action_types_for_persona(persona_config)

    # Step 1 — not envelope-driven: the seeder's normal heartbeat applies.
    if not action_types:
        return {
            "output": False,
            "rationale": "Not an envelope-driven persona; run heartbeat.",
            "self_metric": {"confidence": 1.0, "action_types_count": 0},
        }

    # Step 2 — delegated work waiting: run.
    if has_delegated_work:
        return {
            "output": False,
            "rationale": (
                f"Pending delegated work exists for an envelope-driven persona "
                f"(action_types={action_types}); run heartbeat."
            ),
            "self_metric": {"confidence": 1.0, "action_types_count": len(action_types)},
        }

    # Step 3 — matching inbox action waiting: run.
    if has_pending_action:
        return {
            "output": False,
            "rationale": (
                f"Pending inbox action exists for an envelope-driven persona "
                f"(action_types={action_types}); run heartbeat."
            ),
            "self_metric": {"confidence": 1.0, "action_types_count": len(action_types)},
        }

    # All checks passed — envelope-driven with an empty inbox: skip.
    return {
        "output": True,
        "rationale": (
            f"Envelope-driven persona with no pending delegated work and no "
            f"pending inbox action (action_types={action_types}); skip heartbeat."
        ),
        "self_metric": {"confidence": 1.0, "action_types_count": len(action_types)},
    }


def _load_input() -> dict:
    path = os.environ.get("ORGAN_INPUT")
    raw = open(path).read() if path else sys.stdin.read()
    return json.loads(raw)


def main() -> int:
    try:
        payload = _load_input()
        state = payload.get("state")
        if not isinstance(state, dict):
            raise ValueError("input must contain a 'state' object")
        context = payload.get("context") or {}
    except Exception as e:  # malformed input == the organ itself failed
        print(json.dumps({"error": f"invalid input: {e}"}), file=sys.stderr)
        return 1
    print(json.dumps(decide(state, context), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
