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

## 산출물 위치

```
~/.claude/cache/critique-loop/<run_id>/
  prompt-r1.md       Claude가 쓴 리뷰 요청
  critique-r1.md     Codex가 쓴 비평
  state.json         run 상태
```
