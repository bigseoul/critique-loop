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
    assert payload["prompt_path"] == f"{rid}/prompt-r1.md"
    body = (cache_root / rid / "prompt-r1.md").read_text()
    assert "critique-loop protocol" in body
    assert "round 1 of 3" in body
    assert "critique-r1.md" in body
    assert "VERDICT:" in body
    assert "content X" in body


def test_push_uses_argv_subprocess(monkeypatch, cache_root: Path):
    calls: list[list[str]] = []

    def fake(argv, **kw):
        calls.append(argv)
        class R: stdout = ""; returncode = 0
        return R()

    rc, out, _ = _run(
        monkeypatch, cache_root,
        "push", "--target", "%6",
        "--payload", "@run-x/prompt-r1.md [critique-loop r=1]",
        fake_subproc=fake,
    )
    assert rc == 0
    assert calls[0] == ["tmux", "send-keys", "-t", "%6", "-l",
                        "@run-x/prompt-r1.md [critique-loop r=1]"]
    assert calls[1] == ["tmux", "send-keys", "-t", "%6", "Enter"]


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
