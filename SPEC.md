# critique-loop Skill — Design Spec (v2)

- **Status**: Approved for implementation planning
- **Date**: 2026-05-01
- **Author**: Claude (브레인스토밍 + Codex 2라운드 비평 반영)
- **Spec file**: `docs/superpowers/specs/2026-05-01-critique-loop-design.md`

---

## 1. Overview

`critique-loop`은 Claude Code 페인과 Codex CLI 페인을 같은 tmux window 안에 띄워두고, 두 에이전트가 적대적 코드/설계 리뷰를 N라운드 주고받게 만드는 사용자 글로벌 스킬이다.

오케스트레이션은 Claude 측에서만 일어나고, Codex는 매 라운드 self-contained 프로토콜을 prompt 파일로 받아 평소 작업처럼 비평 JSON을 파일로 쓴다. 통신은 **파일 상태(`manifest.json` + per-round `.done` sentinel)를 단일 source of truth**로 두고, tmux push는 **Codex를 깨우는 단방향 wake 채널**로만 사용한다.

## 2. Goals / Non-Goals

### Goals

- Claude의 한 번의 슬래시 호출(`/critique-loop ...`)로 다라운드 적대적 리뷰를 자동 진행
- 두 페인 모두 사용자가 실시간으로 시청 가능 (TUI 본문이 Codex 페인에 그대로 보임)
- 라운드별 산출물(prompt, critique)이 디스크에 영속되어 사후 검토·재현·재개 가능
- 명시적 종료 조건과 watchdog timeout으로 데드락 방지
- Codex 측 사전 설치 불필요 (프로토콜이 prompt에 self-contained)

### Non-Goals

- Codex CLI를 비대화형(`codex exec`)으로 호출하기 (TUI 페인 시각성을 잃음)
- 합의형/역할분담형 등 비-리뷰 시나리오 (별도 스킬로 분리)
- Claude→Codex가 아닌 다른 에이전트 조합 (예: Gemini 페인) — 본 v2는 Codex 한정
- 영속적 백그라운드 watcher 데몬 — 모든 동기화는 라운드 단위 파일 + ScheduleWakeup
- 합성 결과 자동 코드 변경/PR 생성 — 합성은 보고서 출력까지

## 3. Architecture Summary

```text
┌──────────────── Claude pane ───────────────────┐    ┌─────── Codex pane ────────┐
│  /critique-loop <input>                        │    │                           │
│                                                │    │  (idle ›)                 │
│  ① 입력 정규화 → run_id 생성 → manifest 초기화│    │                           │
│  ② prompt-r1.md.tmp → os.replace →             │    │                           │
│     prompt-r1.done                             │    │                           │
│  ③ Codex pane idle check (capture-pane)        │    │                           │
│  ④ tmux send-keys로 wake 신호 push ───────────────► │  › @<...>/prompt-r1.md   │
│  ⑤ ScheduleWakeup(60s) → 응답 턴 종료          │    │  • 비평 작성 (TUI에 표시)│
│                                                │    │  • critique-r1.json 작성 │
│                                                │    │  • critique-r1.json.done │
│  ⑥ wake → critique-r1.json.done 확인           │    │  (idle ›)                 │
│  ⑦ JSON 파싱 → verdict 판단                    │    │                           │
│     - continue & N<max → Round N+1 (②로)       │    │                           │
│     - done or N==max → ⑧                       │    │                           │
│  ⑧ 합성: 모든 critique-rN.json 종합 → 사용자   │    │                           │
└────────────────────────────────────────────────┘    └───────────────────────────┘
```

**역할 분리:**
- Claude: orchestrator + manifest writer (manifest.json 쓰기 권한 독점)
- Codex: critique writer (critique-rN.json 만 쓰기, manifest 안 건드림)
- → 양쪽 모두 manifest 쓸 일이 없으므로 lockfile 불필요

## 4. File Layout

고정 루트: `~/.claude/cache/critique-loop/`

```text
~/.claude/cache/critique-loop/
└── <run_id>/
    ├── manifest.json           # Claude 만 쓰기. 단일 source of truth.
    ├── prompt-r1.md            # Claude 작성. Codex 읽기.
    ├── prompt-r1.done          # 0-byte sentinel. Claude 작성.
    ├── critique-r1.json        # Codex 작성. Claude 읽기.
    ├── critique-r1.json.done   # 0-byte sentinel. Codex 작성.
    ├── prompt-r2.md
    ├── ...
    └── events.jsonl            # 보조 디버깅 로그 (양쪽 append). Source of truth 아님.
```

`run_id` 정규식: `^\d{8}-\d{6}-[a-z0-9]{6}$` (date-time + 6자 nonce). 생성 외 경로는 모두 거부.

원자성 프로토콜 (양쪽 공통):
1. `<file>.tmp`에 쓴다
2. `os.replace(<file>.tmp, <file>)` (POSIX rename, atomic)
3. `<file>.done` 0-byte 파일을 같은 디렉토리에 생성

읽는 쪽은 `<file>.done` 존재를 확인한 뒤에만 `<file>` 읽는다.

## 5. Protocol

### 5.1 Round 0 — Health Check (default preflight)

`--no-health`로 끄지 않는 한 항상 실행. 본 라운드 진행 전 파이프라인 무결성 검증.

1. Claude가 `prompt-r0.md` 작성:
   ```text
   [critique-loop ping]
   Reply with critique-r0.json containing exactly: {"ping": "pong"}
   Then create critique-r0.json.done sentinel.
   ```
2. Codex pane idle check → push wake 신호
3. ScheduleWakeup(30s)
4. wake → `critique-r0.json` parse, `{"ping": "pong"}` 검증
5. 실패 시 중단 + 안내:
   - 5분 내 .done 없음 → "Codex가 깨어나지 않음. Plan mode 아닌지, 페인 좌표 맞는지 확인."
   - JSON 파싱 실패 → "Codex가 프로토콜을 따르지 않음. 페인이 정말 Codex CLI인지 확인."
   - 검증 통과 → Round 1으로 진입

### 5.2 Round N — Claude turn

1. **prompt 작성**: `prompt-rN.md.tmp` → rename → `prompt-rN.done` 생성
   - 헤더에 protocol contract (§7 참조)
   - 본문에 입력 + 이전 라운드 누적 컨텍스트
2. **manifest 업데이트**: `manifest.json.tmp` → rename. `current_round=N`, `rounds[N-1]`에 새 entry 추가 (`write_status: "ready"`, `verdict: null`)
3. **events.jsonl append**: `{ts, event: "prompt_ready", round: N, request_id}`
4. **Codex pane idle check**:
   ```python
   pane_screen = capture_pane(codex_pane)
   if not is_input_line_empty(pane_screen):
       abort_with_user_prompt("Codex 페인 입력 라인이 비어있지 않습니다. 진행할까요? (y/N)")
   ```
5. **wake 신호 push**:
   ```python
   # prompt_path는 run_dir 하위 상대경로(예: "20260501-150000-a3f9b2/prompt-r1.md")
   # @<path>는 Codex CLI의 file-reference 문법이므로 페이로드의 일부로 허용
   payload = f"@{prompt_path} [critique-loop run={run_id} round={N}]"
   subprocess.run(["tmux", "send-keys", "-t", codex_pane, "-l", payload])
   subprocess.run(["tmux", "send-keys", "-t", codex_pane, "Enter"])
   ```
   - `-l` 리터럴 모드, argv 기반 호출 (셸 이스케이프 사고 차단)
   - 페이로드 허용 항목: `@<prompt_path>` (run_dir 하위 상대경로만), `run_id`, `round`. 그 외 사용자 입력 원문/임의 path 금지.
6. **ScheduleWakeup**: `delaySeconds=60`, `prompt="/critique-loop --resume <run_id>"`
7. Claude 응답 턴 종료 (idle)

### 5.3 Round N — Codex turn

(Codex가 자율적으로 수행, 프로토콜은 prompt 파일 contract에 명시)

1. Codex가 prompt-rN.md 읽고 비평 생성 (TUI에 출력 — 사용자 시청)
2. `critique-rN.json.tmp` → rename → `critique-rN.json.done` 생성
3. (선택) `events.jsonl`에 `{ts, event: "critique_written", round, request_id}` append

### 5.4 Round N — Claude wake

`ScheduleWakeup` 만료 시 Claude 재진입.

```python
if exists(critique_rN_done):
    critique = json.load(critique_rN_path)
    validate_request_id(critique, expected=request_id)  # 중복/지연 신호 차단
    update_manifest(rounds[N-1], write_status="done",
                    verdict=critique["verdict"],
                    critical_count=count_critical(critique["findings"]))
    if critique["verdict"] == "done" or N >= max_rounds:
        goto Synthesis (§5.5)
    else:
        Round N+1 시작 (§5.2)
else:
    elapsed = now - rounds[N-1].started_at
    if elapsed > watchdog_timeout (default 300s):
        report_failure_to_user(round=N, recovery_cmd=f"/critique-loop --resume {run_id}")
        update_manifest(status="timeout")
        return
    else:
        ScheduleWakeup(60s) 재예약
```

### 5.5 Synthesis

마지막 라운드 종료 후 Claude가 모든 `critique-rN.json` 읽어 사용자에게 합성 보고:

```text
## critique-loop 합성 보고 (run_id=...)
- max_rounds=3, completed=2 (early termination: critical=0)
- input: src/example/foo.py

### 라운드 요약
| Round | findings | critical | high | verdict |
|-------|---------|----------|------|---------|
| 1     | 5       | 2        | 1    | continue|
| 2     | 1       | 0        | 0    | done    |

### 처리 결과 (per-finding)
- ✅ Accepted (3): ...
- ❌ Rejected (2): ... (사유)
- ⏸  Deferred (1): ... (별도 이슈/PR)

### 산출물
- ~/.claude/cache/critique-loop/<run_id>/
- 재실행: /critique-loop --resume <run_id>
```

**합성 단계 기본 정책 (구현 강제):**

각 finding은 Claude가 자율 분류하되 다음 default rule을 따른다.

| severity | 기본 처리 | 예외 조건 |
|---|---|---|
| `critical`, `high` | **Accepted** | evidence가 잘못된 파일·라인을 가리키거나 검증 결과 false positive면 Rejected (반드시 reason 필수) |
| `medium` | **Accepted unless** 입력 범위 밖이거나 별도 작업이 더 효율적 | Deferred로 분류 + 사유 |
| `low`, `nit` | **Deferred** | 즉시 수정 가치가 명백하면 Accepted |

Rejected는 항상 reason 필수. 기본 정책으로 분류 불가능한 finding은 사용자에게 (a/r/d) 1회 확인.

자동 코드 변경은 본 스킬 범위 밖 (§13 참조). 합성 보고서까지가 산출물.

## 6. Data Formats

### 6.1 manifest.json (Claude만 쓰기)

```json
{
  "version": "v2",
  "run_id": "20260501-150000-a3f9b2",
  "created_at": "2026-05-01T15:00:00Z",
  "updated_at": "2026-05-01T15:04:32Z",
  "input": {
    "kind": "file",
    "source": "src/example/foo.py",
    "git_sha": "abc1234"
  },
  "max_rounds": 3,
  "current_round": 2,
  "claude_pane": "0:0.0",
  "codex_pane": "0:0.1",
  "watchdog_timeout_sec": 300,
  "status": "in_progress",
  "rounds": [
    {
      "round": 1,
      "request_id": "r1-a3f9b2",
      "prompt_path": "prompt-r1.md",
      "critique_path": "critique-r1.json",
      "write_status": "done",
      "verdict": "continue",
      "critical_count": 2,
      "started_at": "2026-05-01T15:00:01Z",
      "completed_at": "2026-05-01T15:01:30Z"
    }
  ]
}
```

**Status fields (필수 분리):**
- `manifest.status`: 전체 run 상태 (`in_progress` | `done` | `timeout` | `aborted`)
- `rounds[].write_status`: critique 파일 작성 상태 (`pending` | `ready` | `done`)
- `rounds[].verdict`: Codex 판단 (`continue` | `done` | `null` (아직))

이 분리는 Codex round-2 critique에서 명시한 must-fix 항목.

### 6.2 critique-rN.json (Codex만 쓰기)

```json
{
  "request_id": "r1-a3f9b2",
  "round": 1,
  "verdict": "continue",
  "findings": [
    {
      "severity": "critical",
      "title": "race condition in worker pool",
      "evidence": "src/worker/pool.py:142 — shared dict mutated without lock",
      "failure_mode": "Two parallel workers read same task, double-process",
      "recommendation": "Use threading.Lock around task pop/update",
      "verification": "Add test_concurrent_pool with 10 threads"
    }
  ],
  "summary": "1 critical (race), 1 high (silent except), 0 medium"
}
```

**필드 강제:**
- `severity` ∈ {`critical`, `high`, `medium`, `low`, `nit`}
- 각 finding에 5개 필드 모두 필수 (severity, evidence, failure_mode, recommendation, verification)
- `verdict`는 합성 단계에서 wake 로직 분기에 사용

스키마는 prompt 헤더에 명시. Claude가 파싱 실패 시 처리는 §10 참조 (다음 라운드 소비 안 하고 **같은 round repair prompt** 발행).

### 6.3 events.jsonl (양쪽 append, 보조)

```jsonl
{"ts": "2026-05-01T15:00:00Z", "actor": "claude", "event": "run_started", "run_id": "..."}
{"ts": "2026-05-01T15:00:01Z", "actor": "claude", "event": "prompt_ready", "round": 1, "request_id": "r1-..."}
{"ts": "2026-05-01T15:01:30Z", "actor": "codex", "event": "critique_written", "round": 1, "request_id": "r1-..."}
```

손상되어도 manifest + sentinel로 복구 가능. 디버깅·재현용.

## 7. Codex Contract (prompt-rN.md 헤더)

매 prompt 파일 첫 단락에 self-contained protocol contract:

```text
# critique-loop protocol v2
You are an adversarial code reviewer. Your task is round {N} of max {max_rounds}.

## Output
Write your critique to: {critique_path}
Format: JSON matching this schema:
{
  "request_id": "{request_id}",
  "round": {N},
  "verdict": "continue" | "done",
  "findings": [
    { "severity": "critical|high|medium|low|nit",
      "title": "<short>",
      "evidence": "<file:line or quoted text>",
      "failure_mode": "<what breaks and how>",
      "recommendation": "<concrete fix>",
      "verification": "<test or check to confirm fix>" }
  ],
  "summary": "<one-line>"
}

Set verdict="done" only if no critical or high findings remain.

## Completion protocol
1. Write JSON to {critique_path}.tmp
2. os.replace({critique_path}.tmp, {critique_path})
3. Create empty file {critique_path}.done

DO NOT:
- Write to any path outside {run_dir}
- Send tmux commands
- Modify {manifest_path}

## Artifact under review
<원문 또는 누적 컨텍스트>
```

Codex는 평소 자기 작업처럼 파일 읽고/쓰면 끝. tmux 명령 실행 능력 불필요. **Plan mode가 아니어야** 한다는 조건만 충족하면 됨.

## 8. CLI Surface

```bash
/critique-loop                                  # 직전 Claude 메시지/제안 입력
/critique-loop <file>                           # 파일 cat
/critique-loop --diff                           # git diff (현 브랜치 vs base)
/critique-loop "free text proposal"             # 자유 텍스트
/critique-loop --rounds N <input>               # max_rounds 오버라이드 (1..10)
/critique-loop --codex-pane 0:0.1 <input>      # 페인 명시
/critique-loop --no-health <input>              # ⚠ unsafe/debug only — round 0 ping/pong 생략 (wake 채널 미검증, v1 실패 모드 노출)
/critique-loop --watchdog 600 <input>           # watchdog 초 단위 (default 300)
/critique-loop --resume <run_id>                # 중단된 run 재개
/critique-loop --health                          # 단독 health check
/critique-loop --list                            # 최근 run_id 목록
/critique-loop --show <run_id>                   # 특정 run 합성 결과 재출력
```

기본값:
- `max_rounds=3` (절대 상한 10)
- `watchdog_timeout=300` 초 (라운드당)
- `health=on`

## 9. Pane Discovery

```python
self_pane = tmux_display("#{pane_id}")
candidates = [p for p in same_window_panes
              if p.id != self_pane and p.current_command.startswith("codex")]

if --codex-pane explicit:
    use it (validate exists)
elif len(candidates) == 1:
    auto-pick
elif len(candidates) == 0:
    error: "Codex 페인을 같은 window에 띄우고 재시도하세요."
else:
    interactive: 후보 목록 + 페인별 last-line preview 보여주고 사용자 선택
```

자동 선택 기준은 v2에서 명시: "1개일 때만 자동, 그 외엔 사용자 명시 선택" (Codex 비평 반영).

## 10. Error Handling & Recovery

| 상황 | 감지 | 처리 |
|---|---|---|
| Codex 깨어나지 않음 (push 무시) | watchdog 만료 (300s) | 사용자에게 보고 + 페인 상태 가이드 + `--resume <run_id>` |
| Codex가 critique JSON 파싱 실패 또는 schema 위반 | json.load 예외 또는 필드 누락 | **같은 round 내 repair**: `prompt-rN-repair{k}.md` 작성 → critique-rN.json 자리에 다시 쓰도록 wake. `schema_retry_count`(라운드별, max 2) 카운팅. 라운드 자체는 소비 안 함. retry 한도 초과 시 abort. |
| `request_id` 불일치 (지연/중복 신호) | critique["request_id"] != expected | 무시 + events.jsonl 기록. 정상 critique 도착 대기 계속 |
| Codex 페인 사라짐 | tmux list-panes에 없음 | abort + manifest.status=aborted |
| Codex 페인 입력 라인 비어있지 않음 (push 직전) | capture-pane 마지막 줄에 입력 텍스트 감지 | 사용자에게 (y/N) 확인 |
| Plan mode (health에서 발견) | round 0 critique 누락 또는 .done 누락 | 안내: "Codex 페인에서 shift-tab으로 plan mode 해제" |
| max_rounds 도달 (verdict=continue인 채) | round 카운터 == max | 정상 종료. 합성 보고서에 "한도 도달, 미해결 finding 있음" 명시 |
| 사용자 ctrl-c | Claude 응답 중 인터럽트 | manifest.status=aborted. `--resume`으로 이어가기 가능 |

## 11. Safety Constraints

- 모든 파일 경로는 `~/.claude/cache/critique-loop/<run_id>/` 하위로만 (path traversal 차단)
- `run_id` allowlist 외 형식은 거부
- tmux 호출은 항상 argv 기반 `subprocess.run([...])` (셸 문자열 금지)
- push 페이로드 허용 항목: `run_id`, `round`, prompt 파일 reference (`@<prompt_path>`, run_dir 하위 상대경로만). 사용자 입력 원문이나 임의 경로는 금지.
- `manifest.json` 쓰기는 Claude 단독 책임
- `max_rounds` 절대 상한 10
- 호출 시점 cwd의 `CLAUDE.md` / `AGENTS.md` / `.cursor/rules/` 등에 명시된 protected files는 input으로 받아도 비평 보고서 생성하되 자동 변경 절대 안 함 (본 스킬은 어차피 합성 단계까지가 산출물이라 자동 변경 자체가 범위 밖이지만, 보고서에서 권고 시에도 "수정 시 주의" 라벨 명시). 일반적 secrets/lock 파일(`.env*`, `*.lock`, `secrets.*`)은 cwd 설정 없어도 기본 보호.

## 12. Skill Installation

위치: `~/.claude/skills/critique-loop/SKILL.md`

구조 (구현 단계 상세):
```text
~/.claude/skills/critique-loop/
├── SKILL.md                  # frontmatter + 절차 (Claude가 읽는 것)
├── lib/
│   ├── pane.py              # tmux 페인 탐지/idle check/push
│   ├── manifest.py          # manifest.json read/write (atomic)
│   ├── prompt.py            # prompt-rN.md 작성 (헤더 + 본문)
│   ├── critique.py          # critique-rN.json validate/load
│   ├── synthesis.py         # 합성 보고서 생성
│   └── input_resolver.py    # --diff / file / text 자동 추론
└── README.md                 # 사용자용 사용법 (선택)
```

SKILL.md frontmatter 예시:
```yaml
---
name: critique-loop
version: 0.1.0
description: |
  Adversarial review loop between Claude and Codex panes via tmux.
  Codex critiques Claude's artifact (code/diff/spec/text) over N rounds with
  early-termination on critical=0 verdict.
triggers:
  - critique loop
  - codex review loop
  - tmux 리뷰 루프
allowed-tools:
  - Bash
  - Read
  - Write
  - ScheduleWakeup
  - AskUserQuestion
---
```

## 13. Out of Scope (Future Work)

- `codex exec` 비대화형 모드 (TUI 페인 의존성 제거 옵션)
- 합성 결과 → 자동 PR 생성 (별도 스킬: `critique-loop-apply`)
- Claude/Codex 외 다른 에이전트 (Gemini 등) 페어링
- 다중 critic (3+ 에이전트 합의)
- 파일 watcher 기반 wake (fswatch/entr/watchdog) — push가 충분히 신뢰 가능하면 불필요
- 영속 백그라운드 데몬

## 14. Open Questions (구현 단계에서 결정)

- Codex pane idle check 휴리스틱 정확도 — TUI 변형에 얼마나 민감한가? (false positive 시 사용자 (y/N) 확인이 fallback)
- ScheduleWakeup 60s 간격이 cache-warm 한도 내에서 적절한가? (300s를 넘기지 않도록)
- events.jsonl이 실제로 가치 있는가, 아니면 manifest.json 만으로 충분한가? (구현 후 1주 dogfood 후 결정)

## 15. Design History (Traceability)

- **v0** (초안): tmux push가 1차 통신 채널, polling 회피. → Codex round-1 비평: "no" verdict. Push-only deadlock + 입력 충돌 + 셸 이스케이프 + plan mode 휴리스틱 fragile.
- **v1**: 파일 source-of-truth + ScheduleWakeup 폴링으로 구조 반전. push는 보조 알림으로 강등. → Codex round-2 비평: "yes-with-fixes". 5개 must-fix:
  1. Codex 깨어남 메커니즘 부재 (v1의 fantasy 명확화)
  2. status 의미 충돌 (write_status + verdict 분리 필요)
  3. Claude→Codex push Enter 주입 충돌
  4. `--health` 기본 preflight화
  5. critique 구조화 (severity/evidence/failure_mode/recommendation/verification)
- **v2** (본 문서, 초안): must-fix 5개 직접 반영 + Suggested Simplifications 4개 적용 (Claude-only manifest writer로 lockfile 제거, fsync 단순화, events.jsonl 보조 명시, push 책임 정직 인정).
- **v2 patch** (round-3 반영): Codex round-3 비평의 spec 내부 모순 정리 — Architecture Summary fsync 제거, Codex contract `protocol v2`로 통일, push 페이로드 허용 항목 명시 (§5.2/§11), schema 위반은 같은 round repair (§10), 합성 단계 기본 정책 강제 추가 (§5.5), `--no-health`에 unsafe 라벨.

비평 원본:
- `/tmp/critique-loop-design/design.md` (v0)
- `/tmp/critique-loop-design/design-v1.md` (v1)
- Codex 응답: tmux 페인 캡처본 (대화 기록)
