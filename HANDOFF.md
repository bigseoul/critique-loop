# critique-loop — Handoff (resume from new Claude Code session)

> 이 파일은 corp_graph 프로젝트에서 시작된 작업을 이 프로젝트에서 이어가기 위한 메모입니다.
> 새 Claude Code 세션을 이 프로젝트(`/Users/daegyunggang/Documents/workspace/critique-loop`)에서 띄운 뒤, 이 파일을 읽도록 안내하세요.

## 현재 상태 (2026-05-01)

- v0.1.0 구현 중간 단계.
- ✅ Task 1 — Scaffolding (commit `d1bd7e6`)
- ✅ Task 2 — `critique_loop.py` TDD 구현 + 9 tests pass (commit `9b814a7`)
- ✅ SPEC.md 이전 (commit `9a3db0a`)
- ⏳ Task 3 — `SKILL.md` (Claude orchestration 절차)
- ⏳ Task 4 — `README.md` + `SMOKE.md`

`~/.claude/skills/critique-loop` 는 이 디렉토리로의 symlink (Claude Code가 그쪽 경로로 스킬 자동 인식).

## 남은 작업 (이어서 할 것)

### Task 3: `SKILL.md` 작성

위치: `SKILL.md` (이 프로젝트 루트)

frontmatter + Claude orchestration 절차. 핵심 내용:
- Frontmatter: `name: critique-loop`, `version: 0.1.0`, triggers (`critique loop`, `codex review loop`, `코덱스한테 리뷰`), allowed-tools (Bash, Read, Write, ScheduleWakeup)
- 사용자 호출 형식: `/critique-loop`, `--diff`, `<file>`, `"text"`, `--rounds N`, `--codex-pane`, `--no-health`, `--resume <run_id>`, `--health`, `--list`, `--show <run_id>`
- 절차 (CLI 호출 시퀀스):
  1. `pane-discover` → Codex pane id 획득
  2. 입력 resolve (file/diff/text/prior message)
  3. `init` → run_id 획득
  4. Health check (round 0): `health-prompt` → `push` → ScheduleWakeup(30s) → `health-check`
  5. Round N 루프: `prompt` → `push` → ScheduleWakeup(60s) → `check` → 분기 (pending/done-continue/done-done/unknown)
  6. `synthesize` → 사용자에게 출력
- Safety section: payload 형식 강제, max_rounds ≤ 10, protected file 경고

상세는 `SPEC.md` §5 (라운드 프로토콜) 와 §11 (안전 제약) 참고.

### Task 4: `README.md` + `SMOKE.md`

- `README.md`: 사용자 대상 What-it-does, Requirements (tmux, Codex CLI not in Plan mode, Python 3.13), Install, Usage 예시, "v0.1.0은 SPEC의 의도적 축소판" 명시
- `SMOKE.md`: 라이브 Codex pane 대상 수동 체크리스트 — health check, single round, multi-round, pane discovery (없음/복수)

## 새 세션에서 이어갈 때 이 메시지 전달

```
critique-loop 프로젝트야. HANDOFF.md 읽고 남은 Task 3 (SKILL.md), Task 4 (README.md + SMOKE.md) 마저 진행해줘. 각 task 끝에 commit. 자세한 설계는 SPEC.md 참고.
```

## 의도적으로 v0.1.0에서 빼둔 것 (SPEC에는 있지만)

- manifest.json (state.json으로 단순화)
- atomic .tmp + rename + .done sentinel
- schema retry / 구조화 6-field critique JSON
- per-finding accept/reject/defer 정책
- events.jsonl
- pane idle check
- request_id nonce 검증

이유: 개인용 자동화 스킬 스케일에는 over-engineering. 향후 dogfood 후 필요 시 SPEC 항목 단계적으로 도입.

## 외부 의존

- tmux ≥ 3.0 (확인됨)
- Codex CLI (확인됨, default mode 필요)
- Python 3.13+ (시스템 3.14 설치됨, OK)
- pytest 8.4+ (설치됨)
