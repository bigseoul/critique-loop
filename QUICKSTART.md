# critique-loop 빠른 시작

## 준비 (매번)

```
tmux 윈도우 하나에:
  Pane A → claude (Claude Code)
  Pane B → codex (Codex CLI, default mode — "plan" 표시면 shift-tab)
```

## 실행

**Pane A (Claude Code)** 에서 입력:

```text
/critique-loop src/foo.py        # 파일 리뷰
/critique-loop --diff            # 현재 브랜치 변경사항 리뷰
/critique-loop "이 설계 어때?"    # 텍스트 리뷰
```

기본 라운드: **최대 3회** (VERDICT: done 나오면 조기 종료)

## 내부적으로 일어나는 일

```
1. health check  → Codex에 PONG 요청 (30s 대기)
2. round 1       → Codex가 비평 작성 (60s 대기)
3. round 2, 3    → VERDICT: done 나오면 조기 종료
4. 합성 보고서   → Claude가 findings 정리해서 출력
```

## 자주 쓰는 플래그

```text
--rounds 1          라운드 1회만
--no-health         health check 생략 (디버그용)
--resume <run_id>   중단된 run 이어서
--list              지난 run 목록
--show <run_id>     지난 run 결과 다시 보기
```

## 안 되면

| 증상 | 원인 | 해결 |
|---|---|---|
| health check 실패 | Codex가 Plan mode | Codex pane에서 `shift-tab` |
| "no codex pane" | Pane B에 codex 없음 | 같은 윈도우에 `codex` 실행 |
| "multiple codex panes" | codex pane 2개 이상 | `--codex-pane %N` 명시 |
| 중간에 끊김 | Ctrl-C 또는 timeout | `/critique-loop --resume <run_id>` |

## 실행 예 1 — 코드 리뷰

```
[Pane A — Claude]
/critique-loop src/worker.py

→ health check 중... (30초)
→ round 1 비평 대기 중... (60초)

[Pane B — Codex]
  ## Findings
  - severity: critical / where: src/worker.py:42
  - what breaks: shared dict에 lock 없이 접근
  - suggested fix: threading.Lock 추가
  VERDICT: continue

[Pane A — Claude]
→ round 2 비평 대기 중... (60초)
→ VERDICT: done → 합성 보고서 출력
```

## 실행 예 2 — Claude가 제안한 설계를 Codex에게 비평

Claude가 먼저 설계 제안을 출력한 뒤, 인자 없이 `/critique-loop`를 실행하면
**직전 Claude 메시지**가 자동으로 입력으로 잡힌다.

```
[Pane A — Claude]
  캐시 레이어를 Redis 대신 SQLite로 구현하려고 한다.
  이유: 운영 복잡도를 낮추고 싶고, 트래픽이 초당 100건 이하라
  Redis의 네트워크 오버헤드가 오히려 손해라고 판단함.

/critique-loop          ← 인자 없이 실행 → 위 메시지가 입력

→ health check 중... (30초)
→ round 1 비평 대기 중... (60초)

[Pane B — Codex]
  ## Findings
  - severity: high
  - where: 설계 전제
  - what breaks: SQLite는 write lock이 전체 DB를 잠가서
                 동시 write가 몰리면 병목 발생
  - suggested fix: WAL 모드 활성화 또는 write 경합 측정 후 결정
  - how to verify: locust로 동시 write 100건 부하 테스트
  VERDICT: continue

[Pane A — Claude]
→ round 2: WAL 모드 반영한 설계로 재비평 요청
→ VERDICT: done → 합성 보고서 출력
```

## 산출물 위치

```
~/.claude/cache/critique-loop/<run_id>/
  prompt-r1.md       Claude가 쓴 리뷰 요청
  critique-r1.md     Codex가 쓴 비평
  state.json         run 상태
```
