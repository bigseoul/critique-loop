"""Tests for critique-loop. Just the essentials."""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

import critique_loop as cl


# --- helpers ---

def _run(monkeypatch, cache_root: Path, *args, fake_subproc=None) -> tuple[int, str, str]:
    monkeypatch.setattr(cl, "DEFAULT_CACHE_ROOT", cache_root)
    if fake_subproc is not None:
        monkeypatch.setattr(cl.subprocess, "run", fake_subproc)
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    rc = cl.main(list(args))
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture
def cache_root(tmp_path: Path) -> Path:
    p = tmp_path / "cache"
    p.mkdir()
    return p


def _init_run(monkeypatch, cache_root: Path, body: str = "x") -> str:
    rc, out, _ = _run(
        monkeypatch, cache_root,
        "init",
        "--max-rounds", "3",
        "--codex-pane", "%6",
        "--input-source", "src/foo.py",
        "--input-body", body,
    )
    assert rc == 0
    return json.loads(out)["run_id"]


# --- tests ---

def test_init_creates_run_and_state(monkeypatch, cache_root: Path):
    rc, out, _ = _run(
        monkeypatch, cache_root,
        "init",
        "--max-rounds", "3",
        "--codex-pane", "%6",
        "--input-source", "src/foo.py",
        "--input-body", "def x(): return 1",
    )
    assert rc == 0
    payload = json.loads(out)
    rid = payload["run_id"]
    assert re.match(r"^run-\d{8}-\d{6}-[0-9a-f]{6}$", rid)
    state = json.loads((cache_root / rid / "state.json").read_text())
    assert state["max_rounds"] == 3
    assert state["codex_pane"] == "%6"
    assert state["input_source"] == "src/foo.py"
    assert state["round"] == 0


def test_prompt_writes_file_with_protocol_header(monkeypatch, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root, body="content X")
    rc, out, _ = _run(
        monkeypatch, cache_root,
        "prompt", "--run-id", rid, "--round", "1",
        "--prior-summary", "",
    )
    assert rc == 0
    payload = json.loads(out)
    expected_prompt = str(cache_root / rid / "prompt-r1.md")
    expected_critique = str(cache_root / rid / "critique-r1.md")
    assert payload["prompt_path"] == expected_prompt
    body = (cache_root / rid / "prompt-r1.md").read_text()
    assert "critique-loop protocol" in body
    assert "round 1 of 3" in body
    # Critique target must be an absolute path so Codex doesn't `find` for it.
    assert expected_critique in body
    assert "VERDICT:" in body
    assert "content X" in body


def test_push_uses_bracketed_paste_via_tmux_buffer(monkeypatch, cache_root: Path):
    """Payload must arrive as a single bracketed-paste so Codex's Ink/React TUI
    actually registers it as input. send-keys -l does NOT work for that TUI.
    """
    calls: list[tuple[list[str], dict]] = []
    sleeps: list[float] = []

    def fake(argv, **kw):
        calls.append((argv, kw))
        class R: stdout = ""; returncode = 0
        return R()

    monkeypatch.setattr(cl.time, "sleep", lambda s: sleeps.append(s))

    payload = "@run-x/prompt-r1.md [critique-loop r=1]"
    rc, out, _ = _run(
        monkeypatch, cache_root,
        "push", "--target", "%6",
        "--payload", payload,
        fake_subproc=fake,
    )
    assert rc == 0

    # 1) load-buffer reads payload from stdin into a named buffer
    argv0, kw0 = calls[0]
    assert argv0[0:3] == ["tmux", "load-buffer", "-b"]
    assert argv0[-1] == "-"
    assert kw0.get("input") == payload
    buf = argv0[3]

    # 2) paste-buffer pastes that buffer into the target pane (bracketed)
    assert calls[1][0] == ["tmux", "paste-buffer", "-b", buf, "-t", "%6"]

    # 3) cleanup
    assert calls[2][0] == ["tmux", "delete-buffer", "-b", buf]

    # 4) sleep so paste settles before Enter
    assert sleeps, "must sleep between paste and submit"

    # 5) Enter submits
    assert calls[3][0] == ["tmux", "send-keys", "-t", "%6", "Enter"]


def test_push_rejects_payload_with_dangerous_chars(monkeypatch, cache_root: Path):
    calls: list[list[str]] = []

    def fake(argv, **kw):
        calls.append(argv)
        class R: stdout = ""; returncode = 0
        return R()

    rc, _, err = _run(
        monkeypatch, cache_root,
        "push", "--target", "%6",
        "--payload", "rm -rf /; echo gotcha",
        fake_subproc=fake,
    )
    assert rc != 0
    assert "unsafe" in err.lower() or "invalid" in err.lower()
    assert calls == []


def test_check_pending(monkeypatch, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root)
    rc, out, _ = _run(
        monkeypatch, cache_root,
        "check", "--run-id", rid, "--round", "1",
    )
    assert rc == 0
    assert json.loads(out)["state"] == "pending"


def test_check_done_continue(monkeypatch, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root)
    (cache_root / rid / "critique-r1.md").write_text(
        "Some findings here.\n\nVERDICT: continue\n"
    )
    rc, out, _ = _run(monkeypatch, cache_root,
                      "check", "--run-id", rid, "--round", "1")
    payload = json.loads(out)
    assert payload["state"] == "done"
    assert payload["verdict"] == "continue"


def test_check_done_done(monkeypatch, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root)
    (cache_root / rid / "critique-r1.md").write_text(
        "All clear.\n\nVERDICT: done\n"
    )
    rc, out, _ = _run(monkeypatch, cache_root,
                      "check", "--run-id", rid, "--round", "1")
    payload = json.loads(out)
    assert payload["state"] == "done"
    assert payload["verdict"] == "done"


def test_check_no_verdict_returns_done_unknown(monkeypatch, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root)
    (cache_root / rid / "critique-r1.md").write_text("Body without verdict line.\n")
    rc, out, _ = _run(monkeypatch, cache_root,
                      "check", "--run-id", rid, "--round", "1")
    payload = json.loads(out)
    assert payload["state"] == "done"
    assert payload["verdict"] == "unknown"


def test_check_treats_empty_file_as_pending(monkeypatch, cache_root: Path):
    """Half-written file (size==0) must not be reported as done — would
    misclassify a freshly-touched output as a verdict=unknown response.
    """
    rid = _init_run(monkeypatch, cache_root)
    (cache_root / rid / "critique-r1.md").write_text("")
    rc, out, _ = _run(monkeypatch, cache_root,
                      "check", "--run-id", rid, "--round", "1")
    assert json.loads(out)["state"] == "pending"


def test_wait_returns_ready_on_verdict_line(monkeypatch, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root)
    (cache_root / rid / "critique-r1.md").write_text("Findings.\n\nVERDICT: done\n")
    rc, out, _ = _run(monkeypatch, cache_root,
                      "wait", "--run-id", rid, "--round", "1",
                      "--interval", "0.01", "--timeout", "1")
    payload = json.loads(out)
    assert payload["state"] == "ready"
    assert payload["reason"] == "verdict-or-pong"


def test_wait_returns_ready_on_pong_for_round_0(monkeypatch, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root)
    (cache_root / rid / "critique-r0.md").write_text("PONG\n")
    rc, out, _ = _run(monkeypatch, cache_root,
                      "wait", "--run-id", rid, "--round", "0",
                      "--interval", "0.01", "--timeout", "1")
    assert json.loads(out)["state"] == "ready"


def test_wait_timeout_when_file_never_appears(monkeypatch, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root)
    rc, out, _ = _run(monkeypatch, cache_root,
                      "wait", "--run-id", rid, "--round", "1",
                      "--interval", "0.01", "--timeout", "0.05")
    assert json.loads(out)["state"] == "timeout"


def test_wait_size_stable_fallback_for_missing_verdict(monkeypatch, cache_root: Path):
    """If Codex writes a non-empty body without VERDICT line and stops,
    `wait` must still return after size stays stable across the threshold
    (currently 4) consecutive polls — but only after that, not on the first
    stable poll. This is the conservative fallback for non-protocol responses.
    """
    rid = _init_run(monkeypatch, cache_root)
    (cache_root / rid / "critique-r1.md").write_text("body without verdict")
    rc, out, _ = _run(monkeypatch, cache_root,
                      "wait", "--run-id", rid, "--round", "1",
                      "--interval", "0.01", "--timeout", "5")
    payload = json.loads(out)
    assert payload["state"] == "ready"
    assert payload["reason"] == "size-stable"


def test_wait_does_not_short_circuit_on_first_stable_poll(monkeypatch, cache_root: Path):
    """Regression: with a tiny timeout that allows < threshold stable polls,
    wait must time out instead of returning ready. Earlier code returned ready
    after just 2 stable polls, which catches mid-stream pauses as completion.
    """
    rid = _init_run(monkeypatch, cache_root)
    (cache_root / rid / "critique-r1.md").write_text("body without verdict")
    # interval=0.05, timeout=0.06 → only ~1 poll possible, well below threshold (4).
    rc, out, _ = _run(monkeypatch, cache_root,
                      "wait", "--run-id", rid, "--round", "1",
                      "--interval", "0.05", "--timeout", "0.06")
    assert json.loads(out)["state"] == "timeout"


def test_synthesize_flags_rounds_without_verdict(monkeypatch, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root)
    (cache_root / rid / "critique-r1.md").write_text("body, no verdict\n")
    (cache_root / rid / "critique-r2.md").write_text("clean.\n\nVERDICT: done\n")
    rc, out, _ = _run(monkeypatch, cache_root, "synthesize", "--run-id", rid)
    assert "VERDICT 라인 없음" in out  # warning on round 1
    # Round 2 has VERDICT — should not be flagged. Verify by checking that
    # the warning appears exactly once.
    assert out.count("VERDICT 라인 없음") == 1


def test_synthesize_concatenates_all_critiques(monkeypatch, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root)
    (cache_root / rid / "critique-r1.md").write_text("R1: bad.\n\nVERDICT: continue\n")
    (cache_root / rid / "critique-r2.md").write_text("R2: clean.\n\nVERDICT: done\n")
    rc, out, _ = _run(monkeypatch, cache_root,
                      "synthesize", "--run-id", rid)
    assert rc == 0
    assert "Round 1" in out
    assert "Round 2" in out
    assert "R1: bad." in out
    assert "R2: clean." in out
    assert rid in out
