# critique-loop

Adversarial review loop between a Claude Code pane and a Codex CLI pane in the same tmux window.

You hand Claude a file, a `git diff`, or some text. Claude writes a prompt to disk, wakes Codex via `tmux send-keys`, sleeps, then reads Codex's critique back. Repeat for N rounds with early termination, then Claude synthesizes the findings into a single report.

Both panes stay visible. You watch the review happen.

## What it does

```text
┌──────────────── Claude pane ──────────────────┐    ┌─────── Codex pane ────────┐
│  /critique-loop src/foo.py                     │    │   (idle)                  │
│                                                │    │                           │
│  init → run_id=run-20260501-150000-a3f9b2     │    │                           │
│  prompt-r1.md written                          │    │                           │
│  wake push ─────────────────────────────────────►   │  reads prompt-r1.md       │
│  ScheduleWakeup(60s) ... (turn ends)           │    │  writes critique-r1.md    │
│                                                │    │                           │
│  wakes; check → done, verdict=continue         │    │                           │
│  prompt-r2.md ... (loop)                       │    │                           │
│                                                │    │                           │
│  synthesis → final report                      │    │                           │
└────────────────────────────────────────────────┘    └───────────────────────────┘
```

The Codex side needs no install. Each prompt file carries the protocol contract Codex needs to respond — Codex just reads the file you `@`-referenced and writes the markdown critique you asked for.

## Requirements

- **tmux** ≥ 3.0, and you must be running inside it.
- **Codex CLI** in a sibling pane of the same tmux window. **Default mode**, not Plan mode (Codex needs to write files). One Codex pane per window — multiple panes will trigger an interactive picker.
- **Python 3.13+** on `PATH`. (Tested on 3.14.)
- **Claude Code** with `ScheduleWakeup` available — the orchestration relies on time-based hand-offs.

For development:

- `pytest` ≥ 8.4 to run the test suite.

## Install

```bash
# Clone (or already cloned)
cd ~/Documents/workspace/critique-loop

# Symlink into Claude Code's skills directory so the skill auto-registers
mkdir -p ~/.claude/skills
ln -s "$PWD" ~/.claude/skills/critique-loop

# Verify the CLI runs
python3 ./critique_loop.py --help
```

Restart Claude Code (or start a new session) so it picks up the new skill.

## Usage

In any Claude Code session running inside tmux, with a Codex CLI in a sibling pane:

```text
/critique-loop                           # critique the prior Claude message/proposal
/critique-loop src/foo.py                # critique a file
/critique-loop --diff                    # critique current branch's diff vs base
/critique-loop "use sqlite for the cache"   # critique free text
```

Common flags:

```text
--rounds N            override max rounds (1..10, default 3)
--codex-pane %23      explicit pane id when there's more than one Codex pane
--no-health           ⚠ skip the round-0 health check (debug only; usually harmful)
--resume <run_id>     resume an interrupted run
--health              run only the round-0 PONG handshake
--list                list recent run_ids
--show <run_id>       re-print synthesis for a past run
```

### A typical session

1. Open tmux. Pane A: `claude`. Pane B: `codex`. Same window.
2. In pane A: `/critique-loop src/worker/pool.py --rounds 3`
3. Watch Codex's pane: it'll receive `@run-…/prompt-r1.md`, write `critique-r1.md`, idle.
4. Claude wakes itself ~60s later, reads the critique, decides whether to continue.
5. After up to 3 rounds (or earlier if Codex says `VERDICT: done`), Claude prints a synthesis to pane A.

Artifacts live at `~/.claude/cache/critique-loop/<run_id>/`. Inspect them or re-print the synthesis with `/critique-loop --show <run_id>`.

## v0.1.0 scope note

This release is **a deliberate reduction of [SPEC.md](./SPEC.md)** — enough to dogfood the loop end-to-end without spending complexity budget on features that have no failing test cases yet.

What v0.1.0 keeps:

- File-as-source-of-truth + `tmux send-keys` wake channel
- Round-0 health check (PONG handshake)
- N-round loop with early termination on `VERDICT: done`
- Pane discovery (auto-pick 1, error on 0/many)
- Argv-based `tmux` calls + payload allowlist

What v0.1.0 omits (vs. SPEC.md):

- `manifest.json` v2 schema → simplified `state.json`
- Atomic `.tmp` + rename + `.done` sentinel protocol → plain writes
- 6-field structured critique JSON → free-form markdown ending with `VERDICT: continue|done`
- Per-finding accept/reject/defer state machine
- `events.jsonl` event log
- `request_id` nonce verification (drops late/duplicate signals)
- Pane idle check before push
- Schema-repair retries

These will return as needed once dogfooding produces real failure modes. Until then, treat **`critique_loop.py` as authoritative** when behaviour disagrees with SPEC.md.

## Layout

```text
critique-loop/
├── SKILL.md            # what Claude reads to orchestrate the loop
├── critique_loop.py    # the CLI Claude invokes
├── test_critique_loop.py
├── pyproject.toml
├── SPEC.md             # canonical design (target shape; v0.1.0 is a subset)
├── SMOKE.md            # manual end-to-end checklist against a real Codex pane
├── HANDOFF.md          # session-handoff notes (kept across resumes)
└── README.md
```

## Development

```bash
# Run tests
python3 -m pytest -v

# Manual smoke test against a real Codex pane
# See SMOKE.md
```

## License

(none specified — personal-use skill)
