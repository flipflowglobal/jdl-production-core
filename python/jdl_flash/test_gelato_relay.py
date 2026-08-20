"""
Tests for gelato_relay.wait_for_task — the poll loop that decides whether a
relayed trade is treated as executed, failed, or unknown.

Pure: injected poll/sleep functions, no network, no sleeping.
Run: cd python && python3 jdl_flash/test_gelato_relay.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdl_flash import gelato_relay as gr


def main():
    passed = failed = 0

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1; print(f"  ✓ {msg}")
        else:
            failed += 1; print(f"  ✗ {msg}")

    slept = []

    def sleeper(seconds):
        slept.append(seconds)

    def scripted(*states):
        """poll_fn returning each (state, tx) in turn, repeating the last."""
        seq = list(states)

        def poll(_task_id):
            return seq.pop(0) if len(seq) > 1 else seq[0]

        return poll

    # ── terminal states are returned as soon as they appear ──────────────────
    for state in ("ExecSuccess", "ExecReverted", "Cancelled", "Blacklisted"):
        slept.clear()
        got, tx = gr.wait_for_task(
            "t", poll_fn=scripted((state, "0xabc")), sleep_fn=sleeper)
        check(got == state, f"{state} is returned as a terminal state")
        check(slept == [], f"{state} returns without sleeping again")

    check(gr.TERMINAL_STATES == {"ExecSuccess", "ExecReverted", "Cancelled", "Blacklisted"},
          "TERMINAL_STATES is exactly Gelato's four end states")

    # ── polling through transient states until a terminal one ────────────────
    slept.clear()
    got, tx = gr.wait_for_task(
        "t",
        poll_fn=scripted(("CheckPending", None), ("ExecPending", None), ("ExecSuccess", "0xdead")),
        sleep_fn=sleeper,
    )
    check(got == "ExecSuccess" and tx == "0xdead", "polls through transient states to the result")
    check(len(slept) == 2, "sleeps once between each poll, not after the terminal one")

    # ── the regression: a timeout must be distinguishable from a failure ─────
    # wait_for_task used to return the last transient state ("CheckPending"),
    # which the caller could not tell apart from a genuine failure. The task
    # stays executable on Gelato for ~24h, so treating it as "did not execute"
    # both under-records revenue and, on resubmit, races the same userNonce.
    slept.clear()
    got, tx = gr.wait_for_task(
        "t", poll_fn=scripted(("CheckPending", None)), sleep_fn=sleeper, max_polls=5)
    check(got == gr.STATE_TIMEOUT, "giving up returns STATE_TIMEOUT, not a transient state")
    check(got not in gr.TERMINAL_STATES, "STATE_TIMEOUT is not mistakable for a terminal state")
    check(got != "ExecReverted", "STATE_TIMEOUT is not reported as a revert")
    check(len(slept) == 5, "gives up after exactly max_polls attempts")

    # Even when Gelato has told us a hash, an unfinished task is still a timeout —
    # the hash is not proof the trade completed.
    slept.clear()
    got, tx = gr.wait_for_task(
        "t", poll_fn=scripted(("ExecPending", "0xpending")), sleep_fn=sleeper, max_polls=2)
    check(got == gr.STATE_TIMEOUT, "a pending task with a hash is still a timeout")
    check(tx == "0xpending", "the last known hash is still returned for reconciliation")

    # ── an unreachable status endpoint is not a terminal failure either ──────
    # task_status returns ("Unknown", None) when the HTTP call raises.
    slept.clear()
    got, _ = gr.wait_for_task(
        "t", poll_fn=scripted(("Unknown", None)), sleep_fn=sleeper, max_polls=3)
    check(got == gr.STATE_TIMEOUT, "an unreachable status endpoint ends as a timeout, not a revert")

    print(f"\nResults: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
