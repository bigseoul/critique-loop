"""Tests for critique-with-codex. Just the essentials."""
from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path

import pytest

import critique_with_codex as cl


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
    """Interactive mode (default): no --auto, no max_rounds."""
    rc, out, _ = _run(
        monkeypatch, cache_root,
        "init",
        "--codex-pane", "%6",
        "--input-source", "src/foo.py",
        "--input-body", body,
    )
    assert rc == 0
    return json.loads(out)["run_id"]


def _init_run_auto(monkeypatch, cache_root: Path, body: str = "x", rounds: int = 3) -> str:
    """--auto mode: max_rounds required, no interactive checkpoint."""
    rc, out, _ = _run(
        monkeypatch, cache_root,
        "init",
        "--auto", "--max-rounds", str(rounds),
        "--codex-pane", "%6",
        "--input-source", "src/foo.py",
        "--input-body", body,
    )
    assert rc == 0
    return json.loads(out)["run_id"]


# --- tests ---

def test_init_rejects_max_rounds_above_10(monkeypatch, cache_root: Path):
    rc, _, err = _run(
        monkeypatch, cache_root,
        "init", "--auto", "--max-rounds", "11",
        "--codex-pane", "%6",
        "--input-source", "plan.md",
        "--input-body", "x",
    )
    assert rc == 2
    assert "10" in err


def test_init_rejects_max_rounds_zero(monkeypatch, cache_root: Path):
    rc, _, err = _run(
        monkeypatch, cache_root,
        "init", "--auto", "--max-rounds", "0",
        "--codex-pane", "%6",
        "--input-source", "plan.md",
        "--input-body", "x",
    )
    assert rc == 2
    assert "1" in err


def test_save_plan_version_approve_version_mismatch(monkeypatch, tmp_path, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root, body="v1")
    draft_file = tmp_path / "draft.md"
    draft_file.write_text("v2 content")
    _run(monkeypatch, cache_root,
         "save-plan-version", "--run-id", rid, "--version", "2",
         "--draft", "--content-file", str(draft_file))
    rc, _, err = _run(
        monkeypatch, cache_root,
        "save-plan-version", "--run-id", rid, "--version", "3", "--approve",
    )
    assert rc == 2
    assert "mismatch" in err


def test_init_creates_run_and_state(monkeypatch, cache_root: Path):
    rc, out, _ = _run(
        monkeypatch, cache_root,
        "init",
        "--codex-pane", "%6",
        "--input-source", "src/foo.py",
        "--input-body", "def x(): return 1",
    )
    assert rc == 0
    payload = json.loads(out)
    rid = payload["run_id"]
    assert re.match(r"^run-\d{8}-\d{6}-[0-9a-f]{6}$", rid)
    state = json.loads((cache_root / rid / "state.json").read_text())
    assert state["interactive"] is True
    assert state["max_rounds"] is None
    assert state["codex_pane"] == "%6"
    assert state["input_source"] == "src/foo.py"
    assert state["round"] == 0


def test_init_auto_mode_sets_max_rounds(monkeypatch, cache_root: Path):
    rid = _init_run_auto(monkeypatch, cache_root, rounds=5)
    state = json.loads((cache_root / rid / "state.json").read_text())
    assert state["interactive"] is False
    assert state["max_rounds"] == 5


def test_prompt_writes_file_with_protocol_header(monkeypatch, cache_root: Path):
    rid = _init_run_auto(monkeypatch, cache_root, body="content X", rounds=3)
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
    assert "critique-with-codex protocol" in body
    assert "1 of 3" in body
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

    payload = "@run-x/prompt-r1.md [critique-with-codex r=1]"
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


def test_init_auto_trims_to_keep_constant(monkeypatch, cache_root: Path):
    """init must silently delete oldest run directories beyond the auto-trim
    threshold. Survivors are the N **most recently created** (by mtime),
    regardless of run_id name ordering.
    """
    monkeypatch.setattr(cl, "_AUTO_TRIM_KEEP", 3)  # shrink for fast test
    rids: list[str] = [_init_run_auto(monkeypatch, cache_root) for _ in range(5)]
    surviving: set[str] = {
        p.name for p in cache_root.iterdir() if p.name.startswith("run-")
    }
    assert len(surviving) == 3, f"expected 3 survivors, got {len(surviving)}"
    # The last 3 init calls in creation order are the expected survivors.
    expected: set[str] = set(rids[-3:])
    assert surviving == expected, (
        f"survivors mismatch creation order: {surviving} vs {expected}"
    )


def test_init_returned_run_dir_always_exists_after_trim(monkeypatch, cache_root: Path):
    """Regression: prior implementation sorted run dirs by name (random hex
    suffix), so a just-created run could sort as "oldest" and get deleted by
    auto-trim before init even returned. The returned run_dir must always
    exist on disk.
    """
    monkeypatch.setattr(cl, "_AUTO_TRIM_KEEP", 3)
    for _ in range(20):
        rc, out, _ = _run(
            monkeypatch, cache_root,
            "init",
            "--auto", "--max-rounds", "1", "--codex-pane", "%6",
            "--input-source", "x", "--input-body", "x",
        )
        assert rc == 0
        payload = json.loads(out)
        rd = Path(payload["run_dir"])
        assert rd.exists(), f"init returned non-existent run_dir: {rd}"
        assert (rd / "state.json").exists(), "state.json missing in returned run_dir"


def test_auto_trim_uses_mtime_not_name(monkeypatch, cache_root: Path):
    """Two run dirs with names whose lexicographic order is opposite to mtime
    order: the one with newer mtime must survive trim, regardless of name.
    """
    older_name = "run-20260504-100000-zzzzzz"  # name sorts AFTER newer
    newer_name = "run-20260504-100000-000000"  # name sorts BEFORE older
    older_dir = cache_root / older_name
    newer_dir = cache_root / newer_name
    older_dir.mkdir()
    newer_dir.mkdir()
    # Older mtime, then newer mtime, with measurable gap.
    older_mtime = 1_000_000_000
    newer_mtime = 1_000_000_100
    os.utime(older_dir, (older_mtime, older_mtime))
    os.utime(newer_dir, (newer_mtime, newer_mtime))

    monkeypatch.setattr(cl, "DEFAULT_CACHE_ROOT", cache_root)
    cl._auto_trim(cache_root, keep=1)

    assert newer_dir.exists(), "mtime-newest dir was wrongly deleted"
    assert not older_dir.exists(), "mtime-oldest dir was kept"


def test_auto_trim_protects_specified_dir(monkeypatch, cache_root: Path):
    """`protect=` must shield a run dir from deletion even if it ranks as
    oldest by mtime. Defense-in-depth for cmd_init's freshly-made dir.
    """
    # Three dirs, with the one we want to protect having the OLDEST mtime.
    oldest = cache_root / "run-20260504-100000-aaaaaa"
    middle = cache_root / "run-20260504-100000-bbbbbb"
    newest = cache_root / "run-20260504-100000-cccccc"
    for p in (oldest, middle, newest):
        p.mkdir()
    os.utime(oldest, (1_000, 1_000))
    os.utime(middle, (2_000, 2_000))
    os.utime(newest, (3_000, 3_000))

    cl._auto_trim(cache_root, keep=2, protect=oldest)

    # `oldest` must survive; `middle` (which would normally have ranked 2nd
    # newest after `newest`) is evicted in its place.
    assert oldest.exists(), "protect= did not shield oldest"
    assert newest.exists()
    assert not middle.exists(), "expected middle to be evicted in oldest's stead"


def test_init_does_not_trim_when_under_threshold(monkeypatch, cache_root: Path):
    monkeypatch.setattr(cl, "_AUTO_TRIM_KEEP", 5)
    rids = [_init_run(monkeypatch, cache_root) for _ in range(3)]
    surviving = [p.name for p in cache_root.iterdir() if p.name.startswith("run-")]
    assert len(surviving) == 3
    assert set(surviving) == set(rids)


# --- v0.2 tests ---

def test_init_creates_plan_v1(monkeypatch, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root, body="my plan content")
    state = json.loads((cache_root / rid / "state.json").read_text())
    plan_v1 = Path(state["current_plan_path"])
    assert plan_v1.exists()
    assert plan_v1.name == "plan-v1.md"
    assert plan_v1.read_text() == "my plan content"
    assert state["current_plan_version"] == 1
    assert state["draft_plan_path"] is None
    assert state["awaiting_user_review"] is False


def test_prompt_uses_current_plan_path(monkeypatch, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root, body="original plan")
    # Simulate plan update: overwrite plan-v1.md with new content
    state = json.loads((cache_root / rid / "state.json").read_text())
    Path(state["current_plan_path"]).write_text("updated plan content")
    rc, out, _ = _run(
        monkeypatch, cache_root,
        "prompt", "--run-id", rid, "--round", "1", "--prior-summary", "",
    )
    assert rc == 0
    body = (cache_root / rid / "prompt-r1.md").read_text()
    assert "updated plan content" in body
    assert "original plan" not in body


def test_prompt_legacy_run_lazy_creates_plan_v1(monkeypatch, cache_root: Path):
    """pre-v0.2 state (no current_plan_path) must lazily create plan-v1.md."""
    rid = _init_run(monkeypatch, cache_root, body="legacy body")
    state_path = cache_root / rid / "state.json"
    state = json.loads(state_path.read_text())
    del state["current_plan_path"]
    del state["current_plan_version"]
    state_path.write_text(json.dumps(state))
    (cache_root / rid / "plan-v1.md").unlink(missing_ok=True)

    rc, out, _ = _run(
        monkeypatch, cache_root,
        "prompt", "--run-id", rid, "--round", "1", "--prior-summary", "",
    )
    assert rc == 0
    body = (cache_root / rid / "prompt-r1.md").read_text()
    assert "legacy body" in body
    assert (cache_root / rid / "plan-v1.md").exists()
    new_state = json.loads((cache_root / rid / "state.json").read_text())
    assert new_state["current_plan_path"].endswith("plan-v1.md")


def test_save_plan_version_draft_and_approve(monkeypatch, tmp_path, cache_root: Path):
    rid = _init_run(monkeypatch, cache_root, body="v1 content")
    draft_file = tmp_path / "draft.md"
    draft_file.write_text("v2 draft content")

    # Save draft
    rc, out, _ = _run(
        monkeypatch, cache_root,
        "save-plan-version", "--run-id", rid, "--version", "2",
        "--draft", "--content-file", str(draft_file),
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["approved"] is False
    assert payload["version"] == 2
    draft_path = Path(payload["plan_path"])
    assert draft_path.name == "plan-v2.draft.md"
    assert draft_path.exists()
    assert draft_path.read_text() == "v2 draft content"

    state = json.loads((cache_root / rid / "state.json").read_text())
    assert state["awaiting_user_review"] is True
    assert state["draft_plan_path"] == str(draft_path)
    assert state["current_plan_version"] == 1  # not yet approved

    # Approve
    rc, out, _ = _run(
        monkeypatch, cache_root,
        "save-plan-version", "--run-id", rid, "--version", "2", "--approve",
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["approved"] is True
    approved_path = Path(payload["plan_path"])
    assert approved_path.name == "plan-v2.md"
    assert approved_path.exists()
    assert not draft_path.exists()  # draft renamed away

    state = json.loads((cache_root / rid / "state.json").read_text())
    assert state["current_plan_path"] == str(approved_path)
    assert state["current_plan_version"] == 2
    assert state["draft_plan_path"] is None
    assert state["awaiting_user_review"] is False


def test_clean_removes_all_runs(monkeypatch, cache_root: Path):
    rids = [_init_run(monkeypatch, cache_root) for _ in range(3)]
    rc, out, _ = _run(monkeypatch, cache_root, "clean")
    assert rc == 0
    payload = json.loads(out)
    assert payload["count"] == 3
    assert set(payload["deleted"]) == set(rids)
    surviving = [p for p in cache_root.iterdir() if p.name.startswith("run-")]
    assert surviving == []


def test_clean_on_empty_cache(monkeypatch, cache_root: Path):
    rc, out, _ = _run(monkeypatch, cache_root, "clean")
    assert rc == 0
    payload = json.loads(out)
    assert payload == {"deleted": [], "count": 0}


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
