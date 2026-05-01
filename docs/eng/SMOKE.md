# critique-loop — Smoke Test Checklist

Manual end-to-end checks against a **live Codex CLI pane**. Unit tests in `test_critique_loop.py` cover the CLI in isolation; this file covers the parts only a real tmux + Codex session can exercise: pane discovery, the wake channel, the ScheduleWakeup hand-off, and Codex's actual response to the protocol prompt.

Run this whenever you change `critique_loop.py`, `SKILL.md`, or anything that touches the wake/protocol contract.

## Setup (once per session)

1. Start tmux: `tmux new -s critique-test`
2. Split: `Ctrl-b "` (or `Ctrl-b %`)
3. **Pane A**: `claude` (Claude Code, this skill installed)
4. **Pane B**: `codex` (Codex CLI, **default mode** — confirm by checking the bottom-line indicator; if it says "plan", press `shift-tab`)
5. Confirm both panes share one window: `Ctrl-b w` shows them in the same window.

Pre-flight, in pane A:

```bash
python3 ~/.claude/skills/critique-loop/critique_loop.py --help
```

Should print subcommand list. If not, fix install before proceeding.

---

## 1. Pane discovery

### 1a. Single Codex pane (happy path)

- One pane A (claude), one pane B (codex), same window.
- In pane A, type the slash invocation (or use the CLI directly):

```bash
python3 ~/.claude/skills/critique-loop/critique_loop.py pane-discover
```

**Expect**: exit 0, JSON like `{"codex_pane": "%N"}` where `%N` is pane B.

### 1b. No Codex pane

- In pane B, exit Codex (back to a plain shell). Pane B's current command is now `zsh` / `bash`, not `codex`.
- Repeat `pane-discover`.

**Expect**: exit non-zero, stderr mentions "no codex pane in current window".

Restart Codex in pane B before continuing.

### 1c. Multiple Codex panes

- Open a third pane (`Ctrl-b "` again) and start `codex` there too.
- Repeat `pane-discover`.

**Expect**: exit non-zero, stderr lists all candidates and tells you to pass `--codex-pane`.

Close the extra pane (`Ctrl-d` / `exit`) before continuing.

---

## 2. Standalone health check

In pane A, ask Claude:

> /critique-loop --health

Watch pane B: Codex should receive a `@<rid>/prompt-r0.md [critique-loop run=<rid> round=0]` line, open the prompt, and write `PONG` into `~/.claude/cache/critique-loop/<rid>/critique-r0.md`.

Claude should ScheduleWakeup ~30s, then return with a "health OK" message.

**Expect**:
- Pane B visibly receives the `@`-reference and acts on it.
- A `critique-r0.md` file with `PONG` (case-insensitive) appears in the run dir.
- Claude reports health passed.

**If Codex doesn't respond within 30s + 30s retry**:
- Confirm Codex is in default (not Plan) mode.
- Confirm pane B is the right pane id.
- This is the failure mode the health check is designed to catch — Claude should give a clear diagnosis and `--resume` recovery hint.

---

## 3. Single-round review (early termination)

Pick a tiny, clean file — Codex should say `VERDICT: done` on round 1.

```bash
echo 'def add(a, b): return a + b' > /tmp/clean.py
```

In pane A:

> /critique-loop /tmp/clean.py --rounds 3

**Expect**:
- Health round 0 passes (as in §2).
- Round 1 prompt pushed to pane B; Codex writes `critique-r1.md` ending with `VERDICT: done`.
- Claude wakes, sees `done`, **skips rounds 2 and 3**, and prints synthesis.
- Synthesis names `run-…`, lists round 1 only, points to the cache dir.

**Watch for**: Claude does NOT push round 2 or round 3 prompts. The early-termination branch in SKILL.md §5e is the one being tested here.

---

## 4. Multi-round review (full loop)

Pick something Codex will have meaningful objections to over multiple rounds:

```bash
cat > /tmp/messy.py <<'EOF'
import os
def get_user_data(uid):
    cmd = "select * from users where id=" + str(uid)
    os.system("echo " + cmd + " | psql")
    return open("/tmp/results").read()
EOF
```

In pane A:

> /critique-loop /tmp/messy.py --rounds 3

**Expect**:
- Health passes.
- Round 1: Codex flags multiple findings (SQL injection, command injection, file path issues). `VERDICT: continue`.
- Round 2: Claude's `--prior-summary` is non-empty; Codex either flags remaining issues (`continue`) or signs off (`done`).
- Round 3: at most. If `continue` after round 3, synthesis says "max rounds reached, findings unresolved".
- Total wall time roughly `30s health + 60s/round × N` plus Codex's response time.

**Watch for**:
- Each round's prompt file contains a `## Prior rounds` section starting from round 2.
- ScheduleWakeup hand-offs are visible: Claude "ends turn" between rounds and resumes ~60s later.
- The synthesis at the end concatenates all `critique-rN.md` files in order.

---

## 5. Watchdog timeout

Simulate a non-responsive Codex.

In pane A, start a real run:

> /critique-loop /tmp/clean.py

When Codex starts processing, **immediately** in pane B: `Ctrl-c` and let Codex sit idle without responding. Or kill `codex` entirely.

**Expect**:
- Claude wakes at +60s, sees `state=pending`, reschedules.
- After ~300s elapsed for that round, Claude reports a watchdog timeout and prints `--resume <run_id>` recovery instructions.
- Claude does NOT loop forever.

Restart Codex and verify resume works:

> /critique-loop --resume <run_id>

**Expect**: Claude picks up where it left off (re-pushes the in-flight round) without re-running `init` or creating a new run_id.

---

## 6. `--no-health` (sanity check that the flag at least parses)

> /critique-loop /tmp/clean.py --no-health

**Expect**:
- Round 0 is skipped entirely. Claude jumps straight to round 1.
- Otherwise same flow as §3.

⚠ This flag exists for debug. If round 1 fails because the wake channel was actually broken, you'll discover it the hard way — that's why `--no-health` exists but isn't recommended.

---

## 7. `--list` and `--show`

After completing at least one run:

```bash
python3 ~/.claude/skills/critique-loop/critique_loop.py list
```

**Expect**: JSON array of `run-…` ids, newest first.

> /critique-loop --show <run_id>

**Expect**: Synthesis report identical to what was printed at the end of the original run.

---

## 8. Payload safety (CLI-only test, no Codex needed)

Confirms the `push` allowlist is enforced. Pane id doesn't matter — `tmux` is never called because validation fails first.

```bash
python3 ~/.claude/skills/critique-loop/critique_loop.py push \
  --target '%0' --payload 'rm -rf /; echo gotcha'
```

**Expect**: exit 2, stderr says "unsafe/invalid payload". No `tmux` command executed.

This is also covered by `test_push_rejects_payload_with_dangerous_chars` in the unit tests; the smoke version exists so a regression in argv parsing or regex compilation gets caught manually too.

---

## After all checks pass

- Inspect `~/.claude/cache/critique-loop/` — old run dirs should be present and inspectable.
- No orphaned tmux state, no zombie processes.
- Pane B is back at idle Codex prompt.

Record any failure modes that this checklist did NOT catch but would have prevented an incident — they belong here next time.
