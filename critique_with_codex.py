#!/usr/bin/env python3
"""critique-with-codex: adversarial review loop between Claude (this pane) and Codex (sibling pane).

Single-file implementation. CLI subcommands invoked by SKILL.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CACHE_ROOT = Path.home() / ".claude" / "cache" / "critique-with-codex"

# Auto-trim retention: `init` keeps this many most-recent run directories and
# silently removes the rest. Policy is documented in SKILL.md / README.md;
# users who want full history should copy runs out of the cache before they
# fall off the edge.
_AUTO_TRIM_KEEP = 10

# Push payload allowlist: alphanumerics, common path chars, brackets, equals, spaces, @
PAYLOAD_RE = re.compile(r"^@[A-Za-z0-9_./-]+ \[critique-with-codex [A-Za-z0-9_=. -]+\]$")

PROTOCOL_HEADER = """\
# critique-with-codex protocol
You are an adversarial plan/design reviewer. Round {round_info}.

Write your critique to this exact absolute path (do NOT search for it; create the file):
{critique_path}

Format: free-form markdown, with the LAST LINE being exactly one of:
- `VERDICT: continue` (more rounds may help)
- `VERDICT: done` (no critical or high-severity issues remain)

Note: Claude updates the plan each round to reflect your critique before the next round.
Critique the artifact as presented — resolved findings from prior rounds need not be repeated.

Each finding should include: severity (critical/high/medium/low/nit), where (section or quote),
what is wrong or missing (logic gap, unstated assumption, missing edge case, infeasible step,
scope leak, missing dependency, sequencing error), suggested fix, and how to verify the fix.

Do not write to any other path. Do not send tmux commands.
"""

HEALTH_PROMPT = """\
# critique-with-codex protocol — health check
[critique-with-codex ping]

Reply by writing this exact absolute path (do NOT search for it; create the file):
{critique_path}

Contents (verbatim, single line):
PONG
"""


def _now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _generate_run_id() -> str:
    return f"run-{_now_compact()}-{secrets.token_hex(3)}"


def _run_dir(cache_root: Path, run_id: str) -> Path:
    # Light validation: just the prefix and shape
    if not re.match(r"^run-[0-9a-f-]{20,}$", run_id):
        raise ValueError(f"invalid run_id: {run_id!r}")
    return cache_root / run_id


def _load_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text())


def _save_state(run_dir: Path, state: dict) -> None:
    (run_dir / "state.json").write_text(json.dumps(state, indent=2))


def _list_run_dirs(cache_root: Path) -> list[Path]:
    """Return run directories under ``cache_root``, newest first.

    Sorting uses filesystem ``st_mtime_ns`` so the order reflects actual
    creation time rather than the run_id string. The run_id format
    (``run-YYYYMMDD-HHMMSS-<random hex>``) collides on the random suffix
    when multiple runs are created within the same second; lexicographic
    sort would then mis-rank them — including ranking a *just-created* run
    as "old" and exposing it to ``_auto_trim``. mtime sidesteps that.

    Args:
        cache_root: Directory containing ``run-*`` subdirectories. Missing
            directory yields an empty list (not an error).

    Returns:
        List of run-directory paths, ordered newest → oldest.
    """
    if not cache_root.exists():
        return []
    candidates: list[Path] = [
        p for p in cache_root.iterdir()
        if p.is_dir() and p.name.startswith("run-")
    ]
    return sorted(candidates, key=lambda p: p.stat().st_mtime_ns, reverse=True)


def _auto_trim(
    cache_root: Path,
    keep: int | None = None,
    protect: Path | None = None,
) -> None:
    """Delete oldest run directories, retaining at most ``keep`` of them.

    Belt-and-suspenders policy:
      1. Sort by ``st_mtime_ns`` (so "newest" is real wall-clock newest).
      2. Never delete ``protect`` even if it sorts as oldest — guards against
         filesystem clock skew or timestamp ties for a run we just created
         and are about to return to the caller.

    Args:
        cache_root: Cache root containing ``run-*`` subdirectories.
        keep: Maximum number of runs to retain. ``None`` reads the module
            constant ``_AUTO_TRIM_KEEP`` at call time (so tests can
            monkeypatch it).
        protect: A run directory to exclude from deletion regardless of
            its rank. Pass the freshly-created ``run_dir`` from ``cmd_init``.

    Returns:
        None. Side-effect only — runs ``shutil.rmtree`` on each victim.
    """
    if keep is None:
        keep = _AUTO_TRIM_KEEP
    runs: list[Path] = _list_run_dirs(cache_root)
    survivors: list[Path] = runs[:keep]
    victims: list[Path] = runs[keep:]
    if protect is not None and protect not in survivors:
        # protect was about to be deleted: swap it back in, evict the
        # oldest survivor instead so we still honour `keep`.
        if victims:
            evicted: Path = survivors.pop()
            survivors.append(protect)
            victims = [v for v in victims if v != protect]
            victims.append(evicted)
        else:
            # No victims at all: ensure protect is in survivors.
            if protect not in survivors:
                survivors.append(protect)
    for old in victims:
        shutil.rmtree(old)


# --- subcommands ---

def cmd_init(a) -> int:
    rid = _generate_run_id()
    DEFAULT_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    rd = _run_dir(DEFAULT_CACHE_ROOT, rid)
    rd.mkdir()

    interactive = not a.auto
    if interactive and a.max_rounds is not None:
        print("warning: --max-rounds is ignored in interactive mode (use --auto to enable a round limit)", file=sys.stderr)
    if a.max_rounds is not None and (a.max_rounds < 1 or a.max_rounds > 10):
        print(f"error: --max-rounds must be between 1 and 10, got {a.max_rounds}", file=sys.stderr)
        rd.rmdir()
        return 2
    max_rounds = None if interactive else (a.max_rounds if a.max_rounds is not None else 3)

    plan_v1 = rd / "plan-v1.md"
    plan_v1.write_text(a.input_body)

    state = {
        "run_id": rid,
        "round": 0,
        "max_rounds": max_rounds,
        "codex_pane": a.codex_pane,
        "input_source": a.input_source,
        "input_body": a.input_body,
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interactive": interactive,
        "current_plan_path": str(plan_v1),
        "current_plan_version": 1,
        "draft_plan_path": None,
        "awaiting_user_review": False,
    }
    _save_state(rd, state)
    _auto_trim(DEFAULT_CACHE_ROOT, protect=rd)
    print(json.dumps({"run_id": rid, "run_dir": str(rd)}))
    return 0


def cmd_health_prompt(a) -> int:
    rd = _run_dir(DEFAULT_CACHE_ROOT, a.run_id)
    prompt_path = rd / "prompt-r0.md"
    critique_path = rd / "critique-r0.md"
    body = HEALTH_PROMPT.format(critique_path=str(critique_path))
    prompt_path.write_text(body)
    print(json.dumps({"prompt_path": str(prompt_path)}))
    return 0


def cmd_health_check(a) -> int:
    rd = _run_dir(DEFAULT_CACHE_ROOT, a.run_id)
    crit = rd / "critique-r0.md"
    if not crit.exists():
        print(json.dumps({"ok": False, "diagnosis": "no critique-r0.md yet (Codex may be in Plan mode or not responding)"}))
        return 0
    text = crit.read_text().strip()
    if "PONG" in text.upper():
        print(json.dumps({"ok": True, "diagnosis": ""}))
    else:
        print(json.dumps({"ok": False, "diagnosis": f"unexpected health response: {text[:100]!r}"}))
    return 0


def cmd_prompt(a) -> int:
    rd = _run_dir(DEFAULT_CACHE_ROOT, a.run_id)
    state = _load_state(rd)
    prompt_path = rd / f"prompt-r{a.round}.md"
    critique_path = rd / f"critique-r{a.round}.md"

    max_rounds = state.get("max_rounds")
    round_info = f"{a.round} of {max_rounds}" if max_rounds else str(a.round)

    body = PROTOCOL_HEADER.format(round_info=round_info, critique_path=str(critique_path))
    if a.prior_summary.strip():
        body += "\n## Prior rounds\n\n" + a.prior_summary + "\n"

    current_plan_path = state.get("current_plan_path")
    if not current_plan_path:
        # Legacy run (pre-v0.2): lazily materialise plan-v1.md from input_body.
        input_body = state.get("input_body")
        if not input_body:
            print("error: state has neither current_plan_path nor input_body — run init first", file=sys.stderr)
            return 2
        plan_path = rd / "plan-v1.md"
        plan_path.write_text(input_body)
        state["current_plan_path"] = str(plan_path)
        state["current_plan_version"] = 1
        _save_state(rd, state)
    else:
        plan_path = Path(current_plan_path)
    if not plan_path.exists():
        print(f"error: current_plan_path does not exist: {plan_path}", file=sys.stderr)
        return 2
    plan_content = plan_path.read_text()
    plan_source = str(plan_path)

    body += (
        f"\n## Artifact under review\n"
        f"- source: {plan_source}\n\n"
        f"```\n{plan_content}\n```\n"
    )
    prompt_path.write_text(body)
    state["round"] = a.round
    _save_state(rd, state)
    print(json.dumps({"prompt_path": str(prompt_path)}))
    return 0


def cmd_push(a) -> int:
    if not PAYLOAD_RE.match(a.payload):
        print(f"error: unsafe/invalid payload: {a.payload!r}", file=sys.stderr)
        return 2
    # tmux send-keys -l sends one literal key event per char; Codex's Ink/React
    # TUI does not register that as input (chars render but input state stays
    # empty, so any subsequent Enter is a no-op). Use load-buffer + paste-buffer
    # so tmux wraps the payload in bracketed-paste (ESC[200~ ... ESC[201~), which
    # Codex accepts as a single paste block, then send Enter to submit.
    buf = f"critique-with-codex-{os.getpid()}"
    subprocess.run(
        ["tmux", "load-buffer", "-b", buf, "-"],
        input=a.payload, text=True, check=False,
    )
    subprocess.run(["tmux", "paste-buffer", "-b", buf, "-t", a.target], check=False)
    subprocess.run(["tmux", "delete-buffer", "-b", buf], check=False)
    time.sleep(0.3)
    subprocess.run(["tmux", "send-keys", "-t", a.target, "Enter"], check=False)
    print(json.dumps({"ok": True}))
    return 0


def cmd_pane_discover(a) -> int:
    me = subprocess.run(
        ["tmux", "display-message", "-p", "#{pane_id}"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    raw = subprocess.run(
        ["tmux", "list-panes", "-F", "#{pane_id} #{pane_current_command}"],
        capture_output=True, text=True, check=False,
    ).stdout
    candidates = []
    for line in raw.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if pid != me and cmd.startswith("codex"):
            candidates.append(pid)
    if not candidates:
        print("error: no codex pane in current window", file=sys.stderr)
        return 2
    if len(candidates) > 1:
        print(f"error: multiple codex panes: {candidates}; pass --codex-pane explicitly", file=sys.stderr)
        return 2
    print(json.dumps({"codex_pane": candidates[0]}))
    return 0


_VERDICT_RE = re.compile(r"^VERDICT:\s*(continue|done)\s*$")


def _ready_signal(text: str, round_n: int) -> bool:
    """Return True if `text` looks like a complete Codex response.

    Strong signals: VERDICT line for review rounds, PONG for round-0 health.
    Caller falls back to size-stability if neither matches.
    """
    stripped = text.rstrip()
    if not stripped:
        return False
    last = stripped.splitlines()[-1].strip()
    if _VERDICT_RE.match(last):
        return True
    if round_n == 0 and "PONG" in stripped.upper():
        return True
    return False


def cmd_check(a) -> int:
    rd = _run_dir(DEFAULT_CACHE_ROOT, a.run_id)
    crit = rd / f"critique-r{a.round}.md"
    if not crit.exists() or crit.stat().st_size == 0:
        print(json.dumps({"state": "pending"}))
        return 0
    text = crit.read_text().rstrip()
    last = text.splitlines()[-1].strip() if text else ""
    m = _VERDICT_RE.match(last)
    verdict = m.group(1) if m else "unknown"
    print(json.dumps({"state": "done", "verdict": verdict}))
    return 0


# size-stable fallback requires this many consecutive polls with unchanged
# size before declaring ready. With default interval=0.5s, that's ~2s of
# silence — long enough to ride out token-stream pauses but short enough to
# not waste user time when Codex emits non-VERDICT terminators.
_STABLE_POLLS_THRESHOLD = 4


def cmd_wait(a) -> int:
    """Block until Codex finishes writing critique-r{round}.md, or timeout.

    Polls every `interval` seconds. Returns `ready` when the file is non-empty
    AND one of:
      - last non-empty line matches VERDICT: continue|done (review rounds)
      - body contains PONG (round-0 health)
      - file size has been stable for >= _STABLE_POLLS_THRESHOLD consecutive
        polls (conservative fallback for responses without a recognized
        terminator)

    NOTE: Bash tool timeout must be at least (timeout + 20s) * 1000 ms when
    invoking this from Claude Code, or the outer harness will kill the wait
    before it reports back.
    """
    rd = _run_dir(DEFAULT_CACHE_ROOT, a.run_id)
    crit = rd / f"critique-r{a.round}.md"
    start = time.monotonic()
    deadline = start + a.timeout
    last_size = -1
    stable_polls = 0
    while True:
        if crit.exists():
            size = crit.stat().st_size
            if size > 0:
                text = crit.read_text()
                if _ready_signal(text, a.round):
                    elapsed = round(time.monotonic() - start, 2)
                    print(json.dumps({"state": "ready", "elapsed_s": elapsed, "reason": "verdict-or-pong"}))
                    return 0
                if size == last_size:
                    stable_polls += 1
                    if stable_polls >= _STABLE_POLLS_THRESHOLD:
                        elapsed = round(time.monotonic() - start, 2)
                        print(json.dumps({"state": "ready", "elapsed_s": elapsed, "reason": "size-stable"}))
                        return 0
                else:
                    stable_polls = 0
                last_size = size
        if time.monotonic() >= deadline:
            elapsed = round(time.monotonic() - start, 2)
            print(json.dumps({"state": "timeout", "elapsed_s": elapsed}))
            return 0
        time.sleep(a.interval)


def cmd_synthesize(a) -> int:
    rd = _run_dir(DEFAULT_CACHE_ROOT, a.run_id)
    state = _load_state(rd)
    out: list[str] = []
    out.append(f"## critique-with-codex 합성 보고 (run_id={a.run_id})")
    out.append(f"- input: {state['input_source']}")

    current_version = state.get("current_plan_version", 1)
    if current_version > 1:
        chain = " → ".join(f"v{i}" for i in range(1, current_version + 1))
        out.append(f"- Plan versions: {chain}")
        out.append(f"- Final plan: {state.get('current_plan_path', 'N/A')}")
    else:
        out.append(f"- Plan: {state.get('current_plan_path', state['input_source'])}")
    out.append("")

    critique_files = sorted(
        (f for f in rd.glob("critique-r*.md") if f.name != "critique-r0.md"),
        key=lambda p: int(re.search(r"(\d+)", p.name).group(1)),
    )
    last_round = max(
        (int(re.search(r"(\d+)", f.name).group(1)) for f in critique_files), default=0
    )

    for f in critique_files:
        n = int(re.search(r"(\d+)", f.name).group(1))
        text = f.read_text().rstrip()
        last = text.splitlines()[-1].strip() if text else ""
        has_verdict = bool(_VERDICT_RE.match(last))
        out.append(f"### Round {n}")
        if not has_verdict:
            out.append("> ⚠ VERDICT 라인 없음 — size-stable fallback으로 완료 감지. 잘렸거나 프로토콜 미준수 가능성.")
            out.append("")
        out.append(text)
        out.append("")

    out.append("### 산출물")
    out.append(f"- Run dir: {rd}")
    if last_round:
        out.append(f"- Critiques: {rd}/critique-r{{1..{last_round}}}.md")
    print("\n".join(out))
    return 0


def cmd_save_plan_version(a) -> int:
    rd = _run_dir(DEFAULT_CACHE_ROOT, a.run_id)
    state = _load_state(rd)

    if a.action == "draft":
        if not a.content_file:
            print("error: --content-file required with --draft", file=sys.stderr)
            return 2
        content = Path(a.content_file).read_text()
        draft_path = rd / f"plan-v{a.version}.draft.md"
        draft_path.write_text(content)
        state["draft_plan_path"] = str(draft_path)
        state["awaiting_user_review"] = True
        _save_state(rd, state)
        print(json.dumps({"plan_path": str(draft_path), "version": a.version, "approved": False}))
    elif a.action == "approve":
        draft_path_str = state.get("draft_plan_path")
        if not draft_path_str:
            print("error: no draft_plan_path in state; call --draft first", file=sys.stderr)
            return 2
        draft_path = Path(draft_path_str)
        m = re.search(r"plan-v(\d+)\.draft\.md$", draft_path.name)
        if not m or int(m.group(1)) != a.version:
            draft_ver = m.group(1) if m else "?"
            print(f"error: version mismatch — draft is v{draft_ver}, --version {a.version} given", file=sys.stderr)
            return 2
        approved_path = rd / f"plan-v{a.version}.md"
        draft_path.rename(approved_path)
        state["current_plan_path"] = str(approved_path)
        state["current_plan_version"] = a.version
        state["draft_plan_path"] = None
        state["awaiting_user_review"] = False
        _save_state(rd, state)
        print(json.dumps({"plan_path": str(approved_path), "version": a.version, "approved": True}))
    else:  # discard
        draft_path_str = state.get("draft_plan_path")
        if draft_path_str:
            draft_path = Path(draft_path_str)
            if draft_path.exists():
                draft_path.unlink()
        state["draft_plan_path"] = None
        state["awaiting_user_review"] = False
        _save_state(rd, state)
        print(json.dumps({"discarded": True, "current_plan_path": state.get("current_plan_path")}))
    return 0


def cmd_clean(a) -> int:
    """Delete every run directory under the cache root.

    Use this when you want to reset state. Auto-trim (in `init`) handles
    routine pruning; this is the explicit nuke for "start fresh".
    """
    runs = _list_run_dirs(DEFAULT_CACHE_ROOT)
    deleted = []
    for p in runs:
        shutil.rmtree(p)
        deleted.append(p.name)
    print(json.dumps({"deleted": deleted, "count": len(deleted)}))
    return 0


def cmd_list(a) -> int:
    print(json.dumps([p.name for p in _list_run_dirs(DEFAULT_CACHE_ROOT)]))
    return 0


def cmd_state(a) -> int:
    rd = _run_dir(DEFAULT_CACHE_ROOT, a.run_id)
    print(json.dumps(_load_state(rd), indent=2))
    return 0


# --- argparse ---

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="critique-with-codex")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    s.add_argument("--max-rounds", type=int, default=None)
    s.add_argument("--codex-pane", required=True)
    s.add_argument("--input-source", required=True)
    s.add_argument("--input-body", required=True)
    s.add_argument("--auto", action="store_true", default=False)

    s = sub.add_parser("health-prompt")
    s.add_argument("--run-id", required=True)

    s = sub.add_parser("health-check")
    s.add_argument("--run-id", required=True)

    s = sub.add_parser("prompt")
    s.add_argument("--run-id", required=True)
    s.add_argument("--round", type=int, required=True)
    s.add_argument("--prior-summary", default="")

    s = sub.add_parser("push")
    s.add_argument("--target", required=True)
    s.add_argument("--payload", required=True)

    sub.add_parser("pane-discover")

    s = sub.add_parser("check")
    s.add_argument("--run-id", required=True)
    s.add_argument("--round", type=int, required=True)

    s = sub.add_parser("wait")
    s.add_argument("--run-id", required=True)
    s.add_argument("--round", type=int, required=True)
    s.add_argument("--interval", type=float, default=0.5)
    s.add_argument("--timeout", type=float, default=300.0)

    s = sub.add_parser("synthesize")
    s.add_argument("--run-id", required=True)

    s = sub.add_parser("save-plan-version")
    s.add_argument("--run-id", required=True)
    s.add_argument("--version", type=int, required=True)
    grp = s.add_mutually_exclusive_group(required=True)
    grp.add_argument("--draft", dest="action", action="store_const", const="draft")
    grp.add_argument("--approve", dest="action", action="store_const", const="approve")
    grp.add_argument("--discard", dest="action", action="store_const", const="discard")
    s.add_argument("--content-file")

    sub.add_parser("clean")

    sub.add_parser("list")

    s = sub.add_parser("state")
    s.add_argument("--run-id", required=True)

    return p


_DISPATCH = {
    "init": cmd_init,
    "health-prompt": cmd_health_prompt,
    "health-check": cmd_health_check,
    "prompt": cmd_prompt,
    "push": cmd_push,
    "pane-discover": cmd_pane_discover,
    "check": cmd_check,
    "wait": cmd_wait,
    "synthesize": cmd_synthesize,
    "save-plan-version": cmd_save_plan_version,
    "clean": cmd_clean,
    "list": cmd_list,
    "state": cmd_state,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return _DISPATCH[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
