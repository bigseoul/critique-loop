---
name: critique-with-codex
version: 0.2.0
description: |
  코드를 짜기 전 단계에서 계획/설계 문서를 Codex와 함께 반복 정제하는 도구.
  Claude가 N 라운드를 오케스트레이션한다: Codex가 plan을 비평 → Claude가 반영해 새 버전 작성 →
  사용자 승인 → 다음 라운드. 아티팩트가 라운드를 거치며 진화한다.
  코드 diff 리뷰는 /codex를 사용할 것. 이 스킬은 pre-code 단계 전용.
triggers:
  - plan refinement
  - design review loop
  - 설계 문서 정제
  - 계획 리뷰 루프
  - /critique-with-codex
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
---

# critique-with-codex — Claude 오케스트레이션 절차

> **실행 전에 처음부터 끝까지 읽을 것.** 절차는 엄격하다: 파일 쓰기, tmux push, 블로킹 wait은 설명된 순서 그대로 수행해야 한다. health check를 건너뛰거나 wait timeout을 줄이면 조용히 실패한다.

## 이 스킬이 적용되는 경우

사용자가 **코드를 짜기 전** 단계에서 설계 문서, 아키텍처 계획, 스펙, 기능 제안 등을 Codex와 함께 반복 정제하려 할 때 — 보통 `/critique-with-codex`, "plan refinement", "설계 문서 정제", 또는 한국어 동의어로 호출.

**코드 diff 리뷰가 목적이라면 `/codex`를 사용할 것.** 이 스킬은 아티팩트가 라운드를 거쳐 진화해야 의미 있는 pre-code 단계 전용이다.

**사전 조건** (Step 1 전에 확인; 하나라도 실패하면 구체적인 해결책과 함께 중단):

- `tmux`에 접근 가능 (tmux 안에서 실행 중): `tmux display-message -p '#{pane_id}'` 성공.
- **같은 윈도우**의 sibling pane에서 `codex`가 실행 중. (Pane discovery, §절차 Step 1.)
- Codex pane이 **default mode** (Plan mode 아님). Codex가 파일을 써야 한다. health check이 이를 잡는다; 실패하면 사용자에게 `shift-tab`으로 Plan mode 해제 후 `--resume`으로 재실행 안내.
- `python3`이 PATH에 있음.

## CLI 사용법 (사용자가 입력하는 것)

```text
/critique-with-codex plan.md                   # 설계 문서 정제 (인터랙티브, 기본)
/critique-with-codex spec.md                   # 스펙 정제
/critique-with-codex "아키텍처 제안 텍스트"       # 자유 텍스트 정제
/critique-with-codex --auto plan.md            # 비대화형 자동 모드 (max_rounds 도달 시 종료)
/critique-with-codex --auto --rounds N plan.md # --auto 전용 max_rounds 오버라이드 (1..10, 기본 3)
/critique-with-codex --codex-pane <pane_id> plan.md   # pane 명시 (예: %23)
/critique-with-codex --no-health plan.md       # ⚠ round-0 health check 생략 (unsafe; 디버그 전용)
/critique-with-codex --resume <run_id>         # 중단된 run 재개
/critique-with-codex --health                  # standalone health check, 정제 없음
/critique-with-codex --list                    # 최근 run_id 목록
/critique-with-codex --show <run_id>           # 과거 run의 합성 보고서 재출력
```

기본값: **인터랙티브 모드** (max_rounds 없음, 사용자 "stop" 또는 VERDICT=done으로 종료), `watchdog_total=300s` (라운드당), `health=on`.
`--auto` 사용 시: `max_rounds=3` (기본), 체크포인트 생략. **`--auto`도 매 라운드 Claude가 plan을 수정함 — v0.1과 다름.**

## 백킹 CLI

Python 구현체가 이 파일 옆에 있다. 모든 Bash 호출에서 다음 형식 사용:

```bash
CL="python3 \"$HOME/.claude/skills/critique-with-codex/critique_with_codex.py\""
```

서브커맨드 (모두 stdout에 단일 JSON 줄 출력, 에러는 stderr):

| 서브커맨드 | 용도 |
|---|---|
| `pane-discover` | 현재 윈도우에서 sibling codex pane 찾기 |
| `init --codex-pane PID --input-source S --input-body B [--auto] [--max-rounds N]` | run 디렉토리 + `state.json` + `plan-v1.md` 생성; `{run_id, run_dir}` 출력. `--auto` 없으면 interactive 모드 (max_rounds=null). `--auto`면 max_rounds=N (기본 3). |
| `health-prompt --run-id RID` | `prompt-r0.md` (PONG ping) 작성 |
| `health-check --run-id RID` | `critique-r0.md` 읽기; `{ok, diagnosis}` 출력 |
| `prompt --run-id RID --round N [--prior-summary S]` | `prompt-rN.md` 작성. artifact는 `state.current_plan_path` 파일 내용 (매 라운드 최신 승인 plan 사용). |
| `push --target PID --payload P` | bracketed-paste로 Codex 깨우기 (payload를 allowlist 정규식으로 검증) |
| `check --run-id RID --round N` | `critique-rN.md` 검사; `{state: pending|done, verdict?: continue|done|unknown}` 출력. 빈 파일은 pending. |
| `wait --run-id RID --round N [--interval 0.5] [--timeout 300]` | Codex가 `critique-rN.md`를 다 쓸 때까지 블로킹 polling. `{state: ready|timeout, elapsed_s, reason?}` 출력. ready 트리거 우선순위: 1순위 VERDICT 라인 또는 PONG, 2순위 size-stable(연속 4 polls 동안 size 변동 없음 = 기본 ~2초). |
| `save-plan-version --run-id RID --version N (--draft --content-file PATH \| --approve \| --discard)` | draft 관리. `--draft`: content를 `plan-vN.draft.md`로 저장, `awaiting_user_review=true`. `--approve`: draft를 `plan-vN.md`로 rename, `current_plan_path` 갱신. `--discard`: draft 파일 삭제, `awaiting_user_review=false`, `current_plan_path`는 마지막 승인본 유지 (stop 처리용). |
| `synthesize --run-id RID` | plan version chain + 최종 plan 경로 + 모든 critique 출력 |
| `clean` | 모든 run 디렉토리 삭제 (전체 reset). 부분 정리는 `init`의 auto-trim이 처리. |
| `list` | run_id 목록 (최신순) |
| `state --run-id RID` | 전체 `state.json` 출력 |

## Claude ↔ Codex 메시지 모델

**핵심:** 두 pane은 **공유 디렉토리의 파일**로 대화한다. tmux는 메시지 채널이 아니라 "파일 썼으니 읽어봐" 한 번 깨우는 알람일 뿐이다.

```text
        ┌── 명령 채널 (Claude → Codex, 1회성) ─────┐
        │  tmux paste-buffer + Enter             │
        │  payload: @<절대경로> [critique-with-codex ...] │
        │  → Codex의 Ink/React TUI에 1개 입력으로  │
        │    들어가서 사용자가 친 것처럼 처리됨        │
        └────────────────────────────────────────┘
                          │
                          ▼
              ~/.claude/cache/critique-with-codex/<run_id>/
              ├── prompt-rN.md    ◄── Claude write, Codex read
              └── critique-rN.md  ──► Codex write, Claude read (poll)
                          ▲
                          │
        ┌── 응답 채널 (Codex → Claude, file) ──────┐
        │  Codex가 critique-rN.md를 작성             │
        │  Claude의 `wait`이 0.5s 간격 stat polling  │
        │  → VERDICT 라인 / PONG / size-stable로     │
        │    완료 감지 → ready 반환                   │
        └────────────────────────────────────────┘
```

### 두 채널의 명확한 분리

| 채널 | 매체 | 페이로드 | 누가 작성 |
|---|---|---|---|
| **명령 (wake)** | tmux paste-buffer | `@<절대경로> [critique-with-codex run=<rid> round=N]` 한 줄 | Claude (`cmd_push`) |
| **프롬프트 (read)** | 디스크 파일 `prompt-rN.md` | 자기 프로토콜 + 리뷰 대상 본문 | Claude (`cmd_prompt` / `cmd_health_prompt`) |
| **응답** | 디스크 파일 `critique-rN.md` | 마크다운 비평, 마지막 줄 `VERDICT: continue|done` (또는 health round은 `PONG`) | Codex |
| **완료 감지** | 파일 stat poll | — | Claude (`cmd_wait`) |

### 왜 파일이 source of truth

- Codex가 tmux 명령(`tmux wait-for -S ...`)을 실행할 수도 있지만, 권한/샌드박스 상태에 의존. 파일은 항상 동작.
- Codex의 역할을 "프롬프트 읽고 비평 파일 쓰기" 한 가지로 좁힌다 → 프로토콜 단순.
- 비동기 ScheduleWakeup 핸드오프가 60s clamp 때문에 낭비 컸음. file-poll = semantic completion signal이라 실시간.

### 메시지 형식 정확히

**Push payload (Claude → Codex, tmux 한 줄):**
```
@/Users/.../cache/critique-with-codex/run-2026.../prompt-rN.md [critique-with-codex run=run-2026... round=N]
```
- `@<절대경로>` — Codex가 read 트리거
- 정규식 `^@[A-Za-z0-9_./-]+ \[critique-with-codex [A-Za-z0-9_=. -]+\]$` 통과해야 push (CLI 강제)
- **반드시 절대경로** — 상대경로면 Codex가 자기 CWD 기준으로 못 찾고 home dir 전체 `find` 발동 → 권한 다이얼로그 폭주

**Prompt 파일 본문 (review round):**
```markdown
# critique-with-codex protocol
You are an adversarial plan/design reviewer. Round N of M.

Write your critique to this exact absolute path (do NOT search for it):
/Users/.../critique-rN.md

... (포맷 규칙: 마지막 줄은 VERDICT: continue|done)
... (이전 라운드 요약, 리뷰 대상 plan 본문)
```

**Critique 파일 (Codex → Claude):**
```markdown
## Findings

### High: ...
- Severity: ...
- Where: ...
- ...

VERDICT: continue
```

**Health round은 본문이 그냥 `PONG` 한 줄.**

### 메시지 모델에서 따라오는 제약

| 제약 | 이유 |
|---|---|
| 모든 파일 경로는 **절대경로** | Codex가 home dir `find` 시작하는 사고 차단 |
| Push payload는 **bracketed-paste**로만 (paste-buffer) | `tmux send-keys -l`은 Ink/React TUI가 입력으로 인식 안 함 |
| Codex pane은 **default mode** (Plan mode 금지) | Plan mode면 파일 쓰기 거부 → wait 영원히 timeout |
| Codex pane은 **같은 tmux 윈도우의 sibling**만 | `pane-discover`가 같은 윈도우만 스캔 |
| 모든 산출물 `~/.claude/cache/critique-with-codex/<run_id>/` 안에만 | 외부 쓰기 = 프로토콜 위반 |
| Bash 툴 timeout ≥ `(wait timeout + 20) × 1000` ms | 외곽 Bash가 wait보다 먼저 죽으면 state 어중간 |
| Push payload는 CLI가 정규식으로 거부 | 임의 경로/명령 인젝션 차단 |

상세 안전 규칙은 §안전 제약, 메시지가 안 통할 때의 진단은 §에러 처리 참조.

## 절차

### Step 1 — 입력 resolve

사용자 호출 형태에 따라 `(input_source, input_body)` 결정:

| 형태 | `input_source` | `input_body` |
|---|---|---|
| `/critique-with-codex <path>` | `<path>` | 파일 읽기 (Read 툴 사용, `cat` 아님) |
| `/critique-with-codex "..."` | `inline-text` | 따옴표 안의 텍스트 그대로 |

> **코드 diff 리뷰가 목적이면 `/codex`를 사용할 것.** `--diff`나 "직전 메시지 리뷰"는 이 스킬의 범위 밖이다.

resolved body가 비어있거나 ~200 KB 초과이면 진행 전 `AskUserQuestion`으로 사용자에게 확인.

`--health`, `--list`, `--show`, `--resume` — 해당 브랜치로 바로 이동.

### Step 2 — Codex pane 찾기

```bash
eval "$CL pane-discover"
```

- exit 0 + `{"codex_pane": "%N"}` → 사용.
- non-zero + "no codex pane" → 사용자에게: "이 tmux 윈도우의 sibling pane에 Codex CLI를 열고 재실행하세요." 중단.
- non-zero + "multiple codex panes" → `AskUserQuestion`으로 pane id 확인 후 `--codex-pane`으로 지정된 것처럼 진행.

사용자가 `--codex-pane <pid>`를 전달한 경우 discovery 건너뜀 (단, `tmux list-panes -F '#{pane_id}'`로 존재 확인은 필요).

### Step 3 — `init`

**인터랙티브 모드 (기본):**
```bash
eval "$CL init --codex-pane '%N' \
  --input-source 'src/foo.py' --input-body \"$(cat /tmp/cl-input.txt)\""
```

**비대화형 자동 모드 (`--auto`):**
```bash
eval "$CL init --auto --max-rounds 3 --codex-pane '%N' \
  --input-source 'src/foo.py' --input-body \"$(cat /tmp/cl-input.txt)\""
```

body는 임시 파일이나 process substitution으로 전달 — 수 KB 문자열을 셸 명령에 인라인으로 넣지 말 것. JSON 출력을 캡처하고 `run_id`를 run 전체에서 기억.

init은 input_body를 `plan-v1.md`로 저장하고 `state.current_plan_path`를 해당 절대경로로 설정한다. 이후 모든 라운드는 `state.current_plan_path`가 가리키는 파일을 Codex에게 보낸다.

### Step 4 — Health check (round 0)

사용자가 `--no-health`를 전달한 경우만 건너뜀. 그 외에는 필수: 실제 라운드를 소비하기 전에 wake 채널을 검증한다.

```bash
eval "$CL health-prompt --run-id $RID"
# {"prompt_path": "/Users/.../cache/critique-with-codex/<rid>/prompt-r0.md"} 반환 (절대경로)
# push payload는 CLI가 반환한 prompt_path를 그대로 사용:
eval "$CL push --target '%N' --payload \"@$PROMPT_PATH [critique-with-codex run=<rid> round=0]\""
```

> **중요:** payload 안의 `@` 뒤 경로는 **반드시 절대경로**여야 한다. Codex가 자기 CWD를 기준으로 해석해서 home dir까지 `find`로 탐색하는 사고를 막기 위함. CLI는 항상 절대경로 `prompt_path`를 반환하므로 그 값을 그대로 박을 것.

그 다음 **블로킹 wait** (60s 타임아웃):

```bash
eval "$CL wait --run-id $RID --round 0 --timeout 60"
# {"state": "ready"|"timeout", "elapsed_s": N, "reason"?: "..."} 반환
eval "$CL health-check --run-id $RID"
```

`wait`이 `ready`를 반환하면 파일 쓰기가 끝난 상태 — 즉시 `health-check`로 진행. 이 호출은 `ScheduleWakeup` 없이 같은 턴에서 한다.

- `wait` state=`ready` + `health-check` `{"ok": true}` → Step 5 (Round 1)로 진행.
- `wait` state=`timeout` → Codex 무응답. **1회 재시도** 허용: 같은 프롬프트 재push 후 `wait --timeout 60` 한 번 더. 그래도 timeout이면 다음 진단으로 사용자에게 보고 후 중단:
  - "Codex가 응답하지 않음. pane이 맞는지, Codex가 default (not Plan) mode인지 확인 후 `/critique-with-codex --resume <rid>` 재실행."
- `wait` state=`ready` + `health-check` `{"ok": false}` → "Codex pane이 응답했지만 프로토콜을 따르지 않음. 해당 pane이 실제로 Codex CLI인지 확인." 후 중단.

> **왜 ScheduleWakeup 안 쓰나:** 어차피 critique-with-codex 외에 할 일이 없고, ScheduleWakeup의 60s clamp 때문에 Codex가 5초 만에 끝나도 60초 헛도는 낭비가 컸음. file-poll이 곧 semantic completion signal이므로 블로킹 wait이 가장 단순하고 빠름.

> **왜 인터랙티브가 기본인가:** Codex critique → Claude plan 수정은 사용자가 결과를 승인해야 의미가 있음. 매 라운드 사용자 게이트가 있으므로 max_rounds 안전장치가 불필요해짐. `--auto`는 자리를 비울 때 또는 배치 목적의 옵트인. 사용자 응답 분류는 키워드 매칭이 아닌 Claude의 자연어 판단으로 처리 — 모호하면 한 번 재질문.

> **⚠ Bash tool timeout 필수 설정.** `wait --timeout N`을 호출할 때 Bash 툴 인보케이션의 timeout을 최소 `(N + 20) * 1000` ms로 설정해야 한다. 안 그러면 외곽 Bash 툴이 wait보다 먼저 죽어서 state가 어중간하게 남는다. 예: `wait --timeout 300` → Bash `timeout: 320000`. 기본 health round은 `--timeout 60` → Bash `timeout: 80000`.

### Step 5 — Round N 루프

인터랙티브 모드와 --auto 모드가 다르게 동작하므로 주의. 두 모드 모두 5a–5d는 동일.

**5a. prior summary 작성.** 이전 비평을 짧은 컨텍스트 블록으로 요약:

```text
Round 1 verdict: continue (findings 3개, critical 1개)
주요 이슈: <round 1 제목>
... (Codex가 같은 걸 다시 지적하지 않을 만큼만)
```

`--prior-summary`로 전달. Round 1은 `""`로.

**5b. 프롬프트 작성.**

```bash
eval "$CL prompt --run-id $RID --round $N --prior-summary \"$SUMMARY\""
# {"prompt_path": "..."} 반환 (절대경로). artifact = state.current_plan_path 파일 내용.
```

**5c. Push.**

```bash
eval "$CL push --target '%N' --payload \"@$PROMPT_PATH [critique-with-codex run=<rid> round=N]\""
```

exit code 2면 payload 구성 오류 — 수정. **우회 금지**.

**5d. 블로킹 wait + check.**

```bash
eval "$CL wait --run-id $RID --round $N --timeout 300"
eval "$CL check --run-id $RID --round $N"
```

`wait` state에 따른 분기:

| `wait.state` | `verdict` | 액션 |
|---|---|---|
| `ready` | `done` | **5e → 5f/5g (승인 정책) → Step 6**. done이라도 draft 처리 먼저. |
| `ready` | `continue` | 5e → 5f/5g → 5h (다음 라운드 또는 종료). |
| `ready` | `unknown` | 보수적으로 `continue` 처리. 합성에서 표시. |
| `timeout` | — | Watchdog timeout: `--resume <rid>` 복구 힌트와 함께 중단. |

> **주의:** `done`이어도 5e/5f/5g를 건너뛰면 안 된다. `--auto`에서 approve 없이 Step 6으로 가면 synthesize가 이전 승인본을 final로 보고한다.

**5e. Plan 수정 (항상 수행 — 인터랙티브/auto 공통).**

`critique-rN.md` Read 툴로 읽고:
- severity별 finding 파악
- Claude가 plan을 직접 수정 (비판 내용 반영)
- 수정된 plan 텍스트를 임시 파일(`/tmp/plan-draft-rN.md`)에 저장

```bash
eval "$CL save-plan-version --run-id $RID --version $((N+1)) \
  --draft --content-file /tmp/plan-draft-rN.md"
# {"plan_path": ".../plan-v{N+1}.draft.md", "approved": false} 반환
```

**5f. 인터랙티브 체크포인트 (interactive=true인 경우만).**

사용자에게 다음을 표시하고 turn 종료:

```
─── Round N 결과 ───

📋 Codex critique (verdict: continue|done, K findings):
  • [HIGH] ...
  • [MED] ...

✏️ Claude 수정사항 (v{N} → v{N+1}.draft):
  + ...
  - 보류: ...

📌 draft 위치: {plan_path}

승인 / 수정 요청 / 종료 중 하나로 응답하세요.
(명시적으로 "approve"/"승인"도 사용 가능)
```

**5g. 사용자 응답 해석 (다음 turn 시작 시).**

Claude가 자연어로 분류:

| 신호 | 해석 | 액션 |
|---|---|---|
| 승인 ("응", "ok", "approve", "좋아", 빈 응답 등) | 사용자 동의 | `save-plan-version --approve` → draft를 `plan-v{N+1}.md`로 확정. 5h로. |
| 종료 ("stop", "그만", "종료" 등) | 중단 요청 | draft 버림. 현재 `state.current_plan_path` (마지막 승인본)으로 Step 6. |
| 수정 요청 (그 외 텍스트) | 추가 편집 | draft를 사용자 요청 반영해 다시 수정 → `/tmp/plan-draft-rN.md` 갱신 → `save-plan-version --draft` 재호출 → 5f 반복. |
| 모호 ("흠", "좋네" 등) | 의도 불분명 | 한 번만 재질문. 두 번째 응답은 위 3가지로 강제 분류. |

**`--auto` 모드 (5f/5g 생략):** 체크포인트 없이 자동 승인.

```bash
eval "$CL save-plan-version --run-id $RID --version $((N+1)) --approve"
```

**5h. 라운드 진행 또는 종료 결정.**

5f/5g의 사용자 응답이 "종료"이면 바로 Step 6. 아닌 경우:

- VERDICT=done → Step 6 (합성). (승인은 이미 5g에서 완료)
- VERDICT=continue + `--auto` + N==max_rounds → **바로 Step 6** (사용자 질문 없이 종료). unattended 목적이므로 추가 라운드 묻지 않음.
- VERDICT=continue + 인터랙티브 → N++ (사용자 "stop" 신호로만 종료).
- VERDICT=continue + `--auto` + N < max_rounds → N++, 5a로.

**stop 시 draft 상태 정리 (`--discard`):**

```bash
eval "$CL save-plan-version --run-id $RID --version $((N+1)) --discard"
# draft 파일 삭제, awaiting_user_review=false, current_plan_path는 마지막 승인본 유지
```

`wait`의 `reason` 필드 노출 권장 (`size-stable`이면 합성에서 VERDICT 부재 경고 표시).

### Step 6 — 합성

```bash
eval "$CL synthesize --run-id $RID"
```

출력을 사용자에게 표시. synthesize는 plan version chain + 최종 plan 경로 + 모든 critique를 출력한다. **plan 전문은 포함하지 않음 — 파일 경로로 직접 열람.**

Claude가 짧은 후기 추가:
- 진행한 라운드 수, 종료 사유 (VERDICT=done / 사용자 stop / max_rounds 도달)
- 최종 plan 경로 (`state.current_plan_path`)
- 재실행 방법: `/critique-with-codex --resume <run_id>`

## 리뷰 외 호출 브랜치

### `--health` (standalone)

Step 1–4만. health-check 통과(또는 실패) 후 보고하고 종료. 실제 리뷰용 init 없음 — `health-prompt`에 run_id가 필요하므로 플레이스홀더 body로 `init` 호출 가능.

### `--list`

```bash
eval "$CL list"
```

JSON 배열을 친절한 번호 목록으로 사용자에게 출력.

### `--show <run_id>`

```bash
eval "$CL synthesize --run-id <run_id>"
```

먼저 run_id 형식 확인 (반드시 `^run-\d{8}-\d{6}-[0-9a-f]{6}$` 매칭); CLI도 유효하지 않은 형식을 거부한다.

### Cache 정리 (auto-trim + `clean`)

캐시(`~/.claude/cache/critique-with-codex/<run_id>/`)는 두 단계로 관리된다:

1. **Auto-trim (자동, 매 `init`마다):** 새 run 디렉토리 생성 직후 가장 오래된 디렉토리부터 잘라서 **최신 10개만 유지**. 11번째 이후 run을 만들면 가장 오래된 1개가 silent하게 `rmtree`된다. 임계값은 `_AUTO_TRIM_KEEP` 상수 (코드 수정 필요).
2. **`clean` 서브커맨드 (수동):** 모든 run 디렉토리 삭제 (완전 reset).

```bash
eval "$CL clean"
# {"deleted": ["run-...", "run-..."], "count": N} 출력
```

**보존 가치 있는 비평은 캐시 밖으로 옮겨야 한다.** 11번째 run 호출 시 가장 오래된 게 자동 삭제되므로, 캐시는 "최근 작업" 임시 저장소로만 신뢰 가능. 중요한 리뷰는 사용자가 별도 위치(repo 안 또는 메모)로 복사할 책임.

사용자가 "캐시 정리" / "전체 삭제" 등을 요청할 때 `clean` 호출. "자동 삭제 안 되게 해달라"는 요청은 코드 수정 영역 (상수 변경 또는 auto-trim 비활성화).

### `--resume <run_id>`

1. `eval "$CL state --run-id <run_id>"` → state.json 전체 읽기.
2. **`state.awaiting_user_review == true`이면** → "Round N 결과를 다시 보여드릴게요" 후 draft 내용을 요약해 사용자에게 표시. Step 5g(응답 해석)로 바로 진행.
3. 그 외: run 디렉토리에서 가장 높은 `prompt-rK.md`와 `critique-rK.md` 탐색.
   - critique-rK.md 존재 + size>0 → `check` 후 Step 5e로 (plan 수정부터).
   - prompt-rK.md만 존재 → 다시 push + `wait --round K --timeout 300`.
   - 둘 다 없음 → Step 5b부터 재시작.

Resume은 새 run_id를 만들지 않으며 `init`을 재실행하지 않는다.

## 에러 처리

| 증상 | 원인 | 처리 |
|---|---|---|
| `pane-discover`가 codex pane 0개 | 이 윈도우에 Codex 미실행 | 사용자에게 안내; 중단. |
| `pane-discover`가 2개 이상 | Codex 세션 복수 | `AskUserQuestion`으로 선택; `--codex-pane`으로 기록. |
| Health `wait --round 0 --timeout 60` + 1회 재시도 후 timeout | Plan mode, 잘못된 pane, 또는 TUI 멈춤 | `health-check.diagnosis`에 따라 진단; `--resume` 복구 힌트와 함께 중단. |
| `push` exit code 2 ("unsafe/invalid payload") | payload 구성 오류 | 정확한 형식 `@<absolute-prompt-path> [critique-with-codex run=<rid> round=N]`으로 재구성 (CLI가 반환한 절대 `prompt_path`를 그대로 사용). 검증 우회 금지. |
| `wait`가 `state=timeout` 반환 (round 라운드, 기본 300s) | Codex 응답 없음 또는 멈춤 | Watchdog timeout: `wait.elapsed_s`와 `--resume <rid>` 힌트 보고 후 중단. 무한 루프 금지. |
| `check`가 `verdict=unknown` 반환 | Codex가 `VERDICT: continue|done` 없이 종료 | v0.1에는 schema repair 없음. `continue`로 처리 (라운드 1회 추가)하고 합성에서 표시. |
| 사용자 Ctrl-C | — | `/critique-with-codex --resume <rid>`로 state.json에서 중단된 지점 재개 가능. |

## 안전 제약 (위반 금지)

- **Push payload 형식은 CLI가 강제한다.** `push`는 allowlist 정규식에 매칭되지 않는 것을 거부. 임의 사용자 입력이나 임의 경로를 payload에 끼워넣으려 하지 말 것 — `@<absolute-prompt-path>`(CLI가 반환한 `prompt_path`)와 `[critique-with-codex run=<rid> round=N]`만 허용. **절대경로 필수** (Codex가 home dir을 `find`로 스캔하는 사고 방지).
- **`max_rounds` ≤ 10.** 사용자가 더 많이 요청하면 거부하고 설명.
- **모든 캐시는 `~/.claude/cache/critique-with-codex/<run_id>/` 하위에만.** 오케스트레이션 레이어에서 이 트리 밖에 쓰지 말 것.
- **Run_id 형식 강제** `^run-\d{8}-\d{6}-[0-9a-f]{6}$`. `--resume`/`--show`에 다른 형식이 오면 거부.
- **Protected files** (`CLAUDE.md`, `AGENTS.md`, `.cursor/rules/*`, `.env*`, `*.lock`, `secrets.*`) — Codex가 *보고*는 할 수 있지만 Claude가 자동 적용 금지. v0.1.0은 어차피 자동 적용이 없지만, 비평이 protected 경로 수정을 권장하면 합성 보고서에 명시.
- **tmux 호출은 argv 기반.** CLI가 이미 그렇게 한다. 직접 `tmux` 명령을 구성할 일이 생기면 `subprocess.run([...])` 형식 사용, 셸 문자열 금지.
