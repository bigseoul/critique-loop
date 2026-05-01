#!/usr/bin/env python3
"""critique-loop: adversarial review loop between Claude (this pane) and Codex (sibling pane).

Single-file implementation. CLI subcommands invoked by SKILL.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CACHE_ROOT = Path.home() / ".claude" / "cache" / "critique-loop"

# Push payload allowlist: alphanumerics, common path chars, brackets, equals, spaces, @
PAYLOAD_RE = re.compile(r"^@[A-Za-z0-9_./-]+ \[critique-loop [A-Za-z0-9_=. -]+\]$")

PROTOCOL_HEADER = """\
# critique-loop protocol
You are an adversarial code reviewer. This is round {round_n} of {max_rounds}.

Write your critique to: {critique_path}
Format: free-form markdown, with the LAST LINE being exactly one of:
- `VERDICT: continue` (more rounds may help)
- `VERDICT: done` (no critical or high-severity issues remain)

Each finding should include: severity (critical/high/medium/low/nit), where (file:line or quote), what breaks, suggested fix, and how to verify the fix.

Do not write to any other path. Do not send tmux commands.
"""

HEALTH_PROMPT = """\
# critique-loop protocol — health check
[critique-loop ping]

Reply by writing the file: {critique_path}
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


# --- subcommands ---

def cmd_init(a) -> int:
    rid = _generate_run_id()
    DEFAULT_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    rd = _run_dir(DEFAULT_CACHE_ROOT, rid)
    rd.mkdir()
    state = {
        "run_id": rid,
        "round": 0,
        "max_rounds": a.max_rounds,
        "codex_pane": a.codex_pane,
        "input_source": a.input_source,
        "input_body": a.input_body,
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _save_state(rd, state)
    print(json.dumps({"run_id": rid, "run_dir": str(rd)}))
    return 0


def cmd_health_prompt(a) -> int:
    rd = _run_dir(DEFAULT_CACHE_ROOT, a.run_id)
    body = HEALTH_PROMPT.format(critique_path="critique-r0.md")
    (rd / "prompt-r0.md").write_text(body)
    print(json.dumps({"prompt_path": f"{a.run_id}/prompt-r0.md"}))
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
    critique_path = f"critique-r{a.round}.md"
    body = PROTOCOL_HEADER.format(
        round_n=a.round, max_rounds=state["max_rounds"], critique_path=critique_path,
    )
    if a.prior_summary.strip():
        body += "\n## Prior rounds\n\n" + a.prior_summary + "\n"
    body += (
        f"\n## Artifact under review\n"
        f"- source: {state['input_source']}\n\n"
        f"```\n{state['input_body']}\n```\n"
    )
    (rd / f"prompt-r{a.round}.md").write_text(body)
    state["round"] = a.round
    _save_state(rd, state)
    print(json.dumps({"prompt_path": f"{a.run_id}/prompt-r{a.round}.md"}))
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
    buf = f"critique-loop-{os.getpid()}"
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


def cmd_check(a) -> int:
    rd = _run_dir(DEFAULT_CACHE_ROOT, a.run_id)
    crit = rd / f"critique-r{a.round}.md"
    if not crit.exists():
        print(json.dumps({"state": "pending"}))
        return 0
    text = crit.read_text().rstrip()
    last = text.splitlines()[-1].strip() if text else ""
    m = re.match(r"^VERDICT:\s*(continue|done)\s*$", last)
    verdict = m.group(1) if m else "unknown"
    print(json.dumps({"state": "done", "verdict": verdict}))
    return 0


def cmd_synthesize(a) -> int:
    rd = _run_dir(DEFAULT_CACHE_ROOT, a.run_id)
    state = _load_state(rd)
    out: list[str] = []
    out.append(f"## critique-loop 합성 보고 (run_id={a.run_id})")
    out.append(f"- input: {state['input_source']}")
    out.append(f"- max_rounds={state['max_rounds']}")
    out.append("")
    for n in range(1, state["max_rounds"] + 1):
        f = rd / f"critique-r{n}.md"
        if not f.exists():
            continue
        out.append(f"### Round {n}")
        out.append(f.read_text().rstrip())
        out.append("")
    out.append(f"### 산출물")
    out.append(f"- {rd}")
    print("\n".join(out))
    return 0


def cmd_list(a) -> int:
    if not DEFAULT_CACHE_ROOT.exists():
        print(json.dumps([]))
        return 0
    rids = sorted(
        (p.name for p in DEFAULT_CACHE_ROOT.iterdir()
         if p.is_dir() and p.name.startswith("run-")),
        reverse=True,
    )
    print(json.dumps(rids))
    return 0


def cmd_state(a) -> int:
    rd = _run_dir(DEFAULT_CACHE_ROOT, a.run_id)
    print(json.dumps(_load_state(rd), indent=2))
    return 0


# --- argparse ---

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="critique-loop")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    s.add_argument("--max-rounds", type=int, required=True)
    s.add_argument("--codex-pane", required=True)
    s.add_argument("--input-source", required=True)
    s.add_argument("--input-body", required=True)

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

    s = sub.add_parser("synthesize")
    s.add_argument("--run-id", required=True)

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
    "synthesize": cmd_synthesize,
    "list": cmd_list,
    "state": cmd_state,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return _DISPATCH[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
