<!--
⚠ STALE — last synced before the 2026-05-03 rewrite.
The Korean SKILL.md (../../SKILL.md) is the authoritative source.
Notable diff: ScheduleWakeup hand-offs were replaced with the `wait`
subcommand (blocking file-poll). allowed-tools no longer includes
ScheduleWakeup. Bash tool timeout must be >= (wait timeout + 20s) * 1000 ms.
-->
---
name: critique-loop
version: 0.1.0
description: |
  Adversarial review loop between this Claude pane and a sibling Codex CLI pane in
  the same tmux window. Claude orchestrates N rounds: writes a prompt file, wakes
  Codex via tmux send-keys, sleeps with ScheduleWakeup, parses the critique, and
  either continues or synthesizes a final report. Codex receives a self-contained
  protocol in each prompt — no pre-install needed on the Codex side.
triggers:
  - critique loop
  - codex review loop
  - 코덱스한테 리뷰
  - tmux 리뷰 루프
  - /critique-loop
allowed-tools:
  - Bash
  - Read
  - Write
  - ScheduleWakeup
  - AskUserQuestion
---

# critique-loop — Claude orchestration procedure

> Read this top-to-bottom **before** running anything. The procedure is rigid: file writes, tmux pushes, and ScheduleWakeup hand-offs must happen in the exact order described. Skipping the health check or short-circuiting the wake-loop will silently break runs.

## When this skill applies

Invoke when the user asks for an adversarial Codex review of code, a diff, a spec, or free text — typically via `/critique-loop`, "critique loop", "codex review loop", or Korean equivalents.

**Pre-conditions** (verify before step 1; abort with a concrete fix if any fail):

- `tmux` is reachable (running inside tmux): `tmux display-message -p '#{pane_id}'` succeeds.
- A sibling pane in the **same window** is running `codex`. (Pane discovery, §Procedure step 1.)
- The Codex pane is in **default mode**, not Plan mode. (Codex must be allowed to write files. Health check catches this; if it fails, instruct user to `shift-tab` out of Plan mode and re-run with `--resume`.)
- `python3` is on PATH.

## CLI surface (what the user types)

```text
/critique-loop                           # review prior Claude message/proposal
/critique-loop <file>                    # review file contents
/critique-loop --diff                    # review `git diff <base>...HEAD` of current branch
/critique-loop "free text proposal"      # review literal text
/critique-loop --rounds N <input>        # override max_rounds (1..10, default 3)
/critique-loop --codex-pane <pane_id> <input>   # explicit pane (e.g. %23)
/critique-loop --no-health <input>       # ⚠ skip round-0 health check (unsafe; debug only)
/critique-loop --resume <run_id>         # resume an interrupted run
/critique-loop --health                  # standalone health check, no review
/critique-loop --list                    # list recent run_ids
/critique-loop --show <run_id>           # re-print synthesis for a past run
```

Defaults: `max_rounds=3`, `watchdog_total=300s` (per round), `health=on`.

## Backing CLI

The Python implementation lives next to this file. Use this prefix in every Bash call:

```bash
CL="python3 \"$HOME/.claude/skills/critique-loop/critique_loop.py\""
```

Subcommands (all emit single-line JSON on stdout, errors on stderr):

| Subcommand | Purpose |
|---|---|
| `pane-discover` | Find the sibling codex pane in the current window |
| `init --max-rounds N --codex-pane PID --input-source S --input-body B` | Create run dir + `state.json`; prints `{run_id, run_dir}` |
| `health-prompt --run-id RID` | Write `prompt-r0.md` (PONG ping) |
| `health-check --run-id RID` | Read `critique-r0.md`; prints `{ok, diagnosis}` |
| `prompt --run-id RID --round N [--prior-summary S]` | Write `prompt-rN.md` |
| `push --target PID --payload P` | `tmux send-keys -l` + `Enter` to wake Codex (payload validated against allowlist regex) |
| `check --run-id RID --round N` | Inspect `critique-rN.md`; prints `{state: pending|done, verdict?: continue|done|unknown}` |
| `synthesize --run-id RID` | Concatenate all critiques into a single human-readable report |
| `list` | List run_ids (newest first) |
| `state --run-id RID` | Print full `state.json` |

## Procedure

### Step 1 — Resolve input

Decide `(input_source, input_body)` from the user's invocation:

| Form | `input_source` | `input_body` |
|---|---|---|
| `/critique-loop` (no arg) | `prior-message` | text of the immediately prior Claude message/proposal |
| `/critique-loop <path>` | `<path>` | `Read` the file (use the Read tool, not `cat`) |
| `/critique-loop --diff` | `git-diff` | output of `git diff "$(git merge-base HEAD main)...HEAD"` (try `master` if `main` absent) |
| `/critique-loop "..."` | `inline-text` | the quoted text verbatim |

If the resolved body is empty or > ~200 KB, ask the user (`AskUserQuestion`) to confirm or narrow before proceeding.

For `--health`, `--list`, `--show`, `--resume` — skip directly to the matching branch below.

### Step 2 — Discover the Codex pane

```bash
eval "$CL pane-discover"
```

- `0` exit + `{"codex_pane": "%N"}` → use it.
- Non-zero exit + "no codex pane" → tell user: "Open a Codex CLI in a sibling pane of this tmux window, then re-run." Abort.
- Non-zero exit + "multiple codex panes" → ask the user via `AskUserQuestion` for the explicit pane id, then proceed as if `--codex-pane` was given.

If the user passed `--codex-pane <pid>`, skip discovery and use that value (still validate with `tmux list-panes -F '#{pane_id}'` first).

### Step 3 — `init`

```bash
eval "$CL init --max-rounds 3 --codex-pane '%N' \
  --input-source 'src/foo.py' --input-body \"$(cat /tmp/cl-input.txt)\""
```

Pass the body via a temp file or process substitution — never inline a multi-KB string into the shell command. Capture the JSON output; remember `run_id` for the rest of the run.

### Step 4 — Health check (round 0)

Skip iff the user passed `--no-health`. Otherwise this is mandatory: it validates the wake channel before consuming a real round.

```bash
eval "$CL health-prompt --run-id $RID"
# returns {"prompt_path": "<rid>/prompt-r0.md"}
eval "$CL push --target '%N' --payload '@<rid>/prompt-r0.md [critique-loop run=<rid> round=0]'"
```

Then **end your turn with `ScheduleWakeup`**:

- `delaySeconds: 30`
- `prompt`: re-invoke this skill in resume mode, e.g. `/critique-loop --resume <rid>` with a note that you're in the health phase.
- `reason`: "waiting for Codex health PONG".

On wake-up:

```bash
eval "$CL health-check --run-id $RID"
```

- `{"ok": true}` → proceed to Step 5 (Round 1).
- `{"ok": false, "diagnosis": "..."}` → report diagnosis to the user with one of these recoveries, then abort:
  - "no critique-r0.md yet" → "Codex didn't respond. Confirm the pane is the right one, that Codex is in default (not Plan) mode, and re-run `/critique-loop --resume <rid>`."
  - "unexpected health response" → "Codex pane responded but didn't follow the protocol. Confirm the pane is actually running Codex CLI."
  - Allow **one** retry: re-push the same prompt and ScheduleWakeup(30s) once more before declaring failure.

### Step 5 — Round N loop (N = 1..max_rounds)

For each round:

**5a. Build prior summary.** Concatenate prior critiques into a short context block. Keep it terse — Codex will re-read everything from the prompt file:

```text
Round 1 verdict: continue (3 findings, 1 critical)
Top issue: <title from round 1>
... (only enough so Codex doesn't re-flag the same thing)
```

Pass via `--prior-summary`. For round 1, omit it or pass `""`.

**5b. Write the prompt.**

```bash
eval "$CL prompt --run-id $RID --round $N --prior-summary \"$SUMMARY\""
# returns {"prompt_path": "<rid>/prompt-rN.md"}
```

**5c. Push.**

```bash
eval "$CL push --target '%N' --payload '@<rid>/prompt-rN.md [critique-loop run=<rid> round=N]'"
```

`push` rejects any payload that doesn't match `^@[A-Za-z0-9_./-]+ \[critique-loop [A-Za-z0-9_=. -]+\]$`. If you see exit code 2, you constructed the payload wrong — fix it; do **not** bypass.

**5d. End turn with ScheduleWakeup.**

- `delaySeconds: 60`
- `prompt`: `/critique-loop --resume <rid>` with current round.
- `reason`: "waiting for Codex round N critique".

**5e. On wake-up — check.**

```bash
eval "$CL check --run-id $RID --round $N"
```

Branch on the JSON:

| `state` | `verdict` | Action |
|---|---|---|
| `pending` | — | Codex hasn't written yet. If total elapsed for this round < 300s, ScheduleWakeup(60s) again. If ≥ 300s, treat as **watchdog timeout**: report to user with `--resume <rid>` recovery and abort. |
| `done` | `done` | Early termination. Skip remaining rounds and go to Step 6 (synthesis). |
| `done` | `continue` | If `N < max_rounds`, proceed to round `N+1` (back to 5a). If `N == max_rounds`, go to Step 6 and note "max rounds reached, findings unresolved". |
| `done` | `unknown` | Codex wrote a critique but the last line wasn't a valid `VERDICT:` directive. Treat conservatively as `continue`; in the final synthesis, flag the unparseable verdict so the user knows. |

Track elapsed time per round by remembering when you first scheduled the wake; the wake `prompt` should carry enough context (round number + first-wake timestamp) for you to reason about the watchdog.

### Step 6 — Synthesize

```bash
eval "$CL synthesize --run-id $RID"
```

Print the output to the user verbatim, then add a short Claude-authored coda:

- Total rounds run vs. max
- Whether termination was early (verdict=done) or by max_rounds
- Per-finding **classification** following the SPEC §5.5 default policy:
  - `critical`/`high` → **Accepted** (unless evidence is wrong → Rejected with reason)
  - `medium` → **Accepted** unless out-of-scope (then Deferred + reason)
  - `low`/`nit` → **Deferred** unless trivially valuable (then Accepted)
  - Rejected always requires a reason. If a finding is genuinely ambiguous, ask the user (a/r/d) once via `AskUserQuestion`.
- Artifacts path: `~/.claude/cache/critique-loop/<run_id>/`
- How to re-run: `/critique-loop --resume <run_id>`

**Do not auto-apply fixes.** v0.1.0's output is the report; code changes are out of scope.

## Branches for non-review invocations

### `--health` (standalone)

Steps 1–4 only. After health-check passes (or fails), report and stop. No init for a real review — you can call `init` with a tiny placeholder body just to get a run_id, since `health-prompt` requires one.

### `--list`

```bash
eval "$CL list"
```

Print the JSON array as a friendly numbered list to the user.

### `--show <run_id>`

```bash
eval "$CL synthesize --run-id <run_id>"
```

Validate the run_id format first (must match `^run-\d{8}-\d{6}-[0-9a-f]{6}$`); the CLI also rejects invalid forms.

### `--resume <run_id>`

1. `eval "$CL state --run-id <run_id>"` → read current `round`, `max_rounds`, `codex_pane`, `input_source`.
2. Look at the run dir (`~/.claude/cache/critique-loop/<run_id>/`) and find the highest `prompt-rK.md` and the highest `critique-rK.md`.
3. If `critique-rK.md` exists for the latest prompt → call `check` and continue from Step 5e for round K.
4. If only `prompt-rK.md` exists → re-push it and ScheduleWakeup(60s).
5. If neither (or only round 0) → restart the round that was in flight from Step 5b.

Resume must not start a new run_id and must not re-`init`.

## Error handling

| Symptom | Cause | Action |
|---|---|---|
| `pane-discover` finds 0 codex panes | Codex not running in this window | Tell user; abort. |
| `pane-discover` finds 2+ codex panes | Multiple Codex sessions | `AskUserQuestion` to pick one; record as `--codex-pane`. |
| Health round-0 PONG missing after 30s + 30s retry | Codex in Plan mode, wrong pane, or stuck TUI | Diagnose per `health-check.diagnosis`; abort with `--resume` recovery hint. |
| `push` exit code 2 ("unsafe/invalid payload") | You built the payload wrong | Reconstruct with the exact format `@<rid>/prompt-rN.md [critique-loop run=<rid> round=N]`. Never disable validation. |
| `check` keeps returning `pending` past 300s | Codex hung or ignored the wake | Watchdog timeout: report and abort with `--resume <rid>` hint. Do not loop forever. |
| `check` returns `verdict=unknown` | Codex didn't end with `VERDICT: continue|done` | v0.1 has no schema repair. Treat as `continue` (one extra round) and flag in synthesis. |
| User Ctrl-C mid-loop | — | The next `/critique-loop --resume <rid>` picks up where the state.json left off. |

## Safety constraints (do not violate)

- **Push payload format is enforced by the CLI.** `push` rejects anything that doesn't match the allowlist regex. Don't try to embed arbitrary user input or arbitrary paths in the payload — only `@<rid>/prompt-rN.md` and `[critique-loop run=<rid> round=N]` are allowed.
- **`max_rounds` ≤ 10.** If the user asks for more, refuse and explain.
- **All cache lives under `~/.claude/cache/critique-loop/<run_id>/`.** Never write outside this tree from the orchestration layer.
- **Run_id format is enforced** as `^run-\d{8}-\d{6}-[0-9a-f]{6}$`. If a user passes anything else to `--resume`/`--show`, refuse.
- **Protected files** (`CLAUDE.md`, `AGENTS.md`, `.cursor/rules/*`, `.env*`, `*.lock`, `secrets.*`) — Codex may *report* on them but Claude must not auto-apply suggestions. v0.1.0 doesn't auto-apply anything anyway, but flag clearly in the synthesis report if any critique recommends edits to a protected path.
- **tmux invocations are argv-based.** The CLI already does this. If you ever construct a `tmux` command yourself, use `subprocess.run([...])` form (or `tmux <subcommand> -- <arg>` style), never shell strings.

## v0.1.0 deltas from SPEC.md

This skill matches `critique_loop.py` v0.1.0, which deliberately simplifies SPEC.md:

- State file is `state.json`, not `manifest.json` (no v2 manifest schema).
- Critiques are free-form markdown ending with a `VERDICT: continue|done` line — not 6-field JSON.
- Health response is the literal text `PONG` in `critique-r0.md`, not `{"ping": "pong"}` JSON.
- No `.tmp` + rename + `.done` sentinel protocol — plain file writes.
- No `events.jsonl`, no per-finding accept/reject persistence, no schema repair, no `request_id` nonce verification.

When in doubt about behaviour, **trust the CLI** (`critique_loop.py`) over SPEC.md. SPEC.md is the future target; v0.1.0 is the dogfood floor.
