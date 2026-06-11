"""Tests for the persona_inbox_guard organ. Vanilla pytest, stdlib only.

Loaded via importlib so the test file is self-contained and import-path
independent (matches the orchestrator's readiness_check test convention).
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ORGAN = Path(__file__).parent / "organ.py"
SAMPLES = Path(__file__).parent / "samples"

_spec = importlib.util.spec_from_file_location("persona_inbox_guard_organ", ORGAN)
organ = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(organ)


# --------------------------------------------------------------------------
# Contract shape — every return must satisfy the organ contract.
# --------------------------------------------------------------------------
def _assert_contract(result):
    assert isinstance(result, dict)
    assert set(result) >= {"output", "rationale", "self_metric"}
    assert isinstance(result["output"], bool)
    assert isinstance(result["rationale"], str) and result["rationale"]
    sm = result["self_metric"]
    assert isinstance(sm, dict)
    assert "confidence" in sm
    assert isinstance(sm["confidence"], (int, float))
    assert 0.0 <= sm["confidence"] <= 1.0


# --------------------------------------------------------------------------
# Decision branches.
# --------------------------------------------------------------------------
def test_not_envelope_driven_runs_heartbeat():
    """A non-envelope-driven persona (unknown role) always runs."""
    r = organ.decide({
        "persona_config": {"role_id": "analyst"},
        "has_delegated_work": False,
        "has_pending_action": False,
    })
    _assert_contract(r)
    assert r["output"] is False
    assert "Not an envelope-driven" in r["rationale"]
    assert r["self_metric"]["confidence"] == 1.0


def test_envelope_driven_with_delegated_work_runs():
    """Envelope-driven persona with pending delegated work runs."""
    r = organ.decide({
        "persona_config": {"role_id": "researcher"},
        "has_delegated_work": True,
        "has_pending_action": False,
    })
    _assert_contract(r)
    assert r["output"] is False
    assert "delegated work" in r["rationale"]
    assert "run heartbeat" in r["rationale"]


def test_envelope_driven_with_pending_action_runs():
    """Envelope-driven persona with a pending inbox action runs.

    (This is the case the v1 organ's test got wrong — rationale casing.
    Here the assertion matches the actual rationale substring.)
    """
    r = organ.decide({
        "persona_config": {"role_id": "researcher"},
        "has_delegated_work": False,
        "has_pending_action": True,
    })
    _assert_contract(r)
    assert r["output"] is False
    assert "Pending inbox action exists" in r["rationale"]
    assert "run heartbeat" in r["rationale"]


def test_envelope_driven_empty_inbox_skips():
    """Envelope-driven persona with nothing to do skips the heartbeat."""
    r = organ.decide({
        "persona_config": {"role_id": "researcher"},
        "has_delegated_work": False,
        "has_pending_action": False,
    })
    _assert_contract(r)
    assert r["output"] is True
    assert "skip heartbeat" in r["rationale"]
    assert r["self_metric"]["confidence"] == 1.0


def test_explicit_action_types_override_role():
    """Explicit inbox_envelope_action_types override the role_id mapping."""
    r = organ.decide({
        "persona_config": {
            "role_id": "analyst",                       # not in mapping
            "inbox_envelope_action_types": ["review_request"],
        },
        "has_delegated_work": False,
        "has_pending_action": False,
    })
    _assert_contract(r)
    assert r["output"] is True
    assert "skip heartbeat" in r["rationale"]


def test_empty_explicit_action_types_not_envelope_driven():
    """Explicit empty list means NOT envelope-driven (overrides role)."""
    r = organ.decide({
        "persona_config": {
            "role_id": "researcher",
            "inbox_envelope_action_types": [],
        },
        "has_delegated_work": False,
        "has_pending_action": False,
    })
    _assert_contract(r)
    assert r["output"] is False
    assert "Not an envelope-driven" in r["rationale"]


def test_none_config_not_envelope_driven():
    r = organ.decide({
        "persona_config": None,
        "has_delegated_work": False,
        "has_pending_action": False,
    })
    _assert_contract(r)
    assert r["output"] is False
    assert "Not an envelope-driven" in r["rationale"]


def test_missing_io_fields_default_false():
    """Absent has_delegated_work / has_pending_action default to False → skip."""
    r = organ.decide({"persona_config": {"role_id": "researcher"}})
    _assert_contract(r)
    assert r["output"] is True
    assert "skip heartbeat" in r["rationale"]


def test_truthy_non_bool_io_flags_coerced():
    """Non-bool truthy IO facts are coerced (e.g. a count of pending items)."""
    r = organ.decide({
        "persona_config": {"role_id": "researcher"},
        "has_delegated_work": 3,           # truthy
        "has_pending_action": 0,
    })
    _assert_contract(r)
    assert r["output"] is False
    assert "delegated work" in r["rationale"]


def test_action_types_count_in_metric():
    r = organ.decide({
        "persona_config": {"inbox_envelope_action_types": ["a", "b"]},
        "has_delegated_work": False,
        "has_pending_action": False,
    })
    assert r["self_metric"]["action_types_count"] == 2


# --------------------------------------------------------------------------
# Fail-safe / contract hard rules.
# --------------------------------------------------------------------------
def test_malformed_state_fails_safe_to_run():
    for bad in (None, [], "x", 42):
        r = organ.decide(bad)
        _assert_contract(r)
        assert r["output"] is False                     # conservative: run
        assert r["self_metric"]["confidence"] <= 0.5


def test_empty_state_fails_safe_to_run():
    r = organ.decide({})
    _assert_contract(r)
    assert r["output"] is False                         # not envelope-driven
    assert "Not an envelope-driven" in r["rationale"]


def test_deterministic_same_input_same_output():
    state = {"persona_config": {"role_id": "researcher"},
             "has_delegated_work": False, "has_pending_action": False}
    assert organ.decide(dict(state)) == organ.decide(dict(state))


def test_works_without_context_arg():
    """`context` must be optional per the contract."""
    r = organ.decide({"persona_config": {"role_id": "researcher"}})
    _assert_contract(r)


def test_rationale_output_consistency():
    """Rationale 'skip' iff output True; 'run' iff output False."""
    for state in (
        {"persona_config": {"role_id": "researcher"},
         "has_delegated_work": False, "has_pending_action": False},
        {"persona_config": {"role_id": "analyst"}},
        {"persona_config": {"role_id": "researcher"}, "has_delegated_work": True},
    ):
        r = organ.decide(state)
        if r["output"]:
            assert "skip" in r["rationale"]
        else:
            assert "run heartbeat" in r["rationale"] or "conservatively run" in r["rationale"]


# --------------------------------------------------------------------------
# CLI entrypoint — ORGAN_INPUT file + stdin, and every committed sample.
# --------------------------------------------------------------------------
def _run_cli(payload_path=None, stdin_text=None):
    env = dict(os.environ)
    if payload_path is not None:
        env["ORGAN_INPUT"] = str(payload_path)
        proc = subprocess.run([sys.executable, str(ORGAN)], env=env,
                              capture_output=True, text=True)
    else:
        env.pop("ORGAN_INPUT", None)
        proc = subprocess.run([sys.executable, str(ORGAN)], env=env,
                              input=stdin_text, capture_output=True, text=True)
    return proc


def test_cli_stdin_roundtrip():
    payload = json.dumps({"state": {"persona_config": {"role_id": "researcher"},
                                    "has_delegated_work": False,
                                    "has_pending_action": False}})
    proc = _run_cli(stdin_text=payload)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["output"] is True


def test_cli_invalid_input_nonzero_exit():
    proc = _run_cli(stdin_text="not json")
    assert proc.returncode == 1
    assert "invalid input" in proc.stderr


def test_every_sample_runs_through_cli():
    sample_files = sorted(SAMPLES.glob("*.json"))
    assert len(sample_files) >= 3, "expected >=3 committed samples"
    for s in sample_files:
        proc = _run_cli(payload_path=s)
        assert proc.returncode == 0, f"{s.name}: {proc.stderr}"
        out = json.loads(proc.stdout)
        _assert_contract(out)
        # Each sample carries an `expect` block describing the verdict.
        spec = json.loads(s.read_text())
        exp = spec.get("expect")
        if exp is not None:
            assert out["output"] == exp["output"], s.name
            assert exp["rationale_contains"] in out["rationale"], s.name


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
