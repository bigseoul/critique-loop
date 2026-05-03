# critique-loop — Smoke Test 체크리스트

**live Codex CLI pane** 대상 수동 end-to-end 검증. `test_critique_loop.py`의 단위 테스트는 CLI를 격리 환경에서 커버하고, 이 파일은 실제 tmux + Codex 세션에서만 확인 가능한 것들을 다룬다: pane discovery, push 채널 (bracketed-paste), file-poll `wait`의 실제 latency, 그리고 Codex의 실제 프로토콜 응답.

`critique_loop.py`, `SKILL.md`, 또는 wake/protocol 계약에 닿는 변경을 할 때마다 실행.

## 초기 설정 (세션당 1회)

1. tmux 시작: `tmux new -s critique-test`
2. 분할: `Ctrl-b "` (또는 `Ctrl-b %`)
3. **Pane A**: `claude` (Claude Code, 이 스킬 설치 상태)
4. **Pane B**: `codex` (Codex CLI, **default mode** — 하단 표시줄 "plan" 이면 `shift-tab` 눌러 해제)
5. 두 pane이 같은 윈도우인지 확인: `Ctrl-b w`에서 같은 윈도우에 표시되어야 함.

Pane A에서 사전 확인:

```bash
python3 ~/.claude/skills/critique-loop/critique_loop.py --help
```

서브커맨드 목록이 출력되어야 함. 안 되면 설치 먼저 수정.

---

## 1. Pane discovery

### 1a. Codex pane 1개 (정상 경로)

- Pane A (claude) 1개, Pane B (codex) 1개, 같은 윈도우.
- Pane A에서:

```bash
python3 ~/.claude/skills/critique-loop/critique_loop.py pane-discover
```

**기대 결과**: exit 0, `{"codex_pane": "%N"}` JSON (N은 Pane B).

### 1b. Codex pane 없음

- Pane B에서 Codex 종료 (일반 셸로 돌아감). Pane B의 current command가 `zsh` / `bash`.
- `pane-discover` 재실행.

**기대 결과**: non-zero exit, stderr에 "no codex pane in current window" 포함.

계속 진행 전 Pane B에서 Codex 재시작.

### 1c. Codex pane 2개 이상

- 세 번째 pane 열기 (`Ctrl-b "`) 후 `codex` 시작.
- `pane-discover` 재실행.

**기대 결과**: non-zero exit, stderr에 모든 후보 목록과 `--codex-pane` 명시 요구.

계속 진행 전 추가 pane 닫기 (`Ctrl-d` / `exit`).

---

## 2. 단독 health check

Pane A에서 Claude에게:

> /critique-loop --health

Pane B 관찰: Codex가 `@<rid>/prompt-r0.md [critique-loop run=<rid> round=0]`을 받아 프롬프트를 열고 `~/.claude/cache/critique-loop/<rid>/critique-r0.md`에 `PONG`을 써야 한다.

Claude가 `wait --round 0 --timeout 60`으로 블로킹하다가 보통 5~30s 안에 "health OK" 메시지로 돌아온다 (이전엔 ScheduleWakeup ~30s 고정).

**기대 결과**:
- Pane B에서 `@`-참조를 받고 실제로 처리하는 게 보임.
- run 디렉토리에 `PONG` (대소문자 무관)이 담긴 `critique-r0.md` 파일 생성.
- Claude가 health 통과를 보고.

**Codex가 30s + 30s 재시도 후에도 응답 없으면**:
- Codex가 default (Plan 아님) mode인지 확인.
- Pane B가 맞는 pane id인지 확인.
- 이게 health check이 잡으려는 실패 모드다 — Claude가 명확한 진단과 `--resume` 복구 힌트를 줘야 함.

---

## 3. 단일 라운드 리뷰 (조기 종료)

작고 깔끔한 파일 선택 — Codex가 round 1에서 `VERDICT: done`을 내릴 것.

```bash
echo 'def add(a, b): return a + b' > /tmp/clean.py
```

Pane A에서:

> /critique-loop /tmp/clean.py --rounds 3

**기대 결과**:
- Health round 0 통과 (§2 참고).
- Round 1 프롬프트가 Pane B로 push됨; Codex가 `VERDICT: done`으로 끝나는 `critique-r1.md` 작성.
- Claude가 깨어나 `done`을 확인하고 **round 2, 3를 건너뛰고** 합성 출력.
- 합성이 `run-…` 이름, round 1만 나열, 캐시 디렉토리 경로 표시.

**확인 포인트**: Claude가 round 2, 3 프롬프트를 push하지 않는다. SKILL.md §5e의 조기 종료 브랜치가 테스트되는 것.

---

## 4. 다중 라운드 리뷰 (전체 루프)

Codex가 여러 라운드에 걸쳐 의미 있는 지적을 할 만한 코드 선택:

```bash
cat > /tmp/messy.py <<'EOF'
import os
def get_user_data(uid):
    cmd = "select * from users where id=" + str(uid)
    os.system("echo " + cmd + " | psql")
    return open("/tmp/results").read()
EOF
```

Pane A에서:

> /critique-loop /tmp/messy.py --rounds 3

**기대 결과**:
- Health 통과.
- Round 1: Codex가 복수의 finding (SQL injection, command injection, 파일 경로 문제) 지적. `VERDICT: continue`.
- Round 2: Claude의 `--prior-summary`가 비어있지 않음; Codex가 남은 이슈 지적 (`continue`) 또는 완료 (`done`).
- Round 3: 최대. Round 3 후에도 `continue`이면 합성에 "max rounds 도달, finding 미해결" 표시.
- 총 소요 시간 ≈ Codex 응답 시간 합 (file-poll로 즉시 진행. 이전 `ScheduleWakeup(30s health + 60s/round)` 고정 비용 사라짐).

**확인 포인트**:
- Round 2부터 프롬프트 파일에 `## Prior rounds` 섹션 포함.
- 라운드 사이에 ScheduleWakeup 턴 종료 없이 같은 Bash 콜에서 연속 진행 (`push → wait → check`).
- 합성이 모든 `critique-rN.md`를 순서대로 연결. VERDICT 라인 없는 라운드는 ⚠ 경고 표시.

---

## 5. Watchdog timeout

응답 없는 Codex 시뮬레이션.

Pane A에서 실제 run 시작:

> /critique-loop /tmp/clean.py

Codex가 처리를 시작하면, **즉시** Pane B에서: `Ctrl-c`로 중단하거나 `codex` 아예 종료.

**기대 결과**:
- Claude가 +60s에 깨어나 `state=pending` 확인 후 재예약.
- 해당 라운드 경과 ~300s 후 Claude가 watchdog timeout 보고 + `--resume <run_id>` 복구 안내 출력.
- Claude가 무한 루프하지 않는다.

Codex 재시작 후 resume 동작 확인:

> /critique-loop --resume <run_id>

**기대 결과**: 진행 중이던 라운드를 다시 push, 새 run_id 생성이나 `init` 재실행 없이.

---

## 6. `--no-health` (플래그 파싱만 확인)

> /critique-loop /tmp/clean.py --no-health

**기대 결과**:
- Round 0 완전 생략. Claude가 바로 round 1로 진입.
- 이후 흐름은 §3과 동일.

⚠ 이 플래그는 디버그용. Wake 채널이 실제로 깨져있었다면 round 1에서 발견하게 된다 — `--no-health`가 존재하지만 권장하지 않는 이유.

---

## 7. `--list`와 `--show`

최소 한 번 run 완료 후:

```bash
python3 ~/.claude/skills/critique-loop/critique_loop.py list
```

**기대 결과**: `run-…` id 배열 JSON, 최신순.

> /critique-loop --show <run_id>

**기대 결과**: 원래 run 종료 시 출력된 합성 보고서와 동일.

---

## 8. Payload safety (CLI 단독 테스트, Codex 불필요)

`push` allowlist 강제 확인. Pane id는 중요하지 않음 — 검증 실패로 `tmux`가 호출되지 않는다.

```bash
python3 ~/.claude/skills/critique-loop/critique_loop.py push \
  --target '%0' --payload 'rm -rf /; echo gotcha'
```

**기대 결과**: exit 2, stderr에 "unsafe/invalid payload". `tmux` 명령 미실행.

단위 테스트의 `test_push_rejects_payload_with_dangerous_chars`도 같은 케이스를 커버하지만, smoke 버전은 argv 파싱이나 정규식 컴파일 회귀가 수동으로도 잡히도록 유지.

---

## 모든 체크 통과 후

- `~/.claude/cache/critique-loop/` 확인 — 이전 run 디렉토리가 있고 검사 가능한 상태.
- 고아 tmux 상태 없음, 좀비 프로세스 없음.
- Pane B가 idle Codex 프롬프트 상태로 복귀.

이 체크리스트로는 못 잡았지만 실제로 문제가 됐을 실패 모드가 있다면 여기에 추가.
