# critique-loop

같은 tmux 윈도우 안의 Claude Code pane과 Codex CLI pane이 적대적 코드 리뷰를 주고받는 스킬.

파일, `git diff`, 또는 자유 텍스트를 넘기면 Claude가 프롬프트를 디스크에 쓰고, `tmux paste-buffer`로 Codex를 깨우고, 잠든 뒤 Codex의 비평을 읽는다. N 라운드 반복하다 조기 종료 조건 충족 시 합성 보고서를 출력한다.

두 pane 모두 화면에 그대로 보인다. 리뷰가 실시간으로 진행되는 걸 눈으로 확인할 수 있다.

## 동작 방식

```text
┌──────────────── Claude pane ──────────────────┐    ┌─────── Codex pane ────────┐
│  /critique-loop src/foo.py                     │    │   (idle)                  │
│                                                │    │                           │
│  init → run_id=run-20260501-150000-a3f9b2     │    │                           │
│  prompt-r1.md 작성                              │    │                           │
│  push ──────────────────────────────────────────►   │  prompt-r1.md 읽음        │
│  wait (file-poll, ~2s 간격) ...                 │    │  critique-r1.md 작성      │
│                                                │    │                           │
│  ready; check → done, verdict=continue          │    │                           │
│  prompt-r2.md ... (루프)                       │    │                           │
│                                                │    │                           │
│  synthesis → 최종 보고서                        │    │                           │
└────────────────────────────────────────────────┘    └───────────────────────────┘
```

Codex 쪽에는 별도 설치가 없어도 된다. 각 프롬프트 파일에 프로토콜 계약이 self-contained로 포함되어 있어서 Codex는 `@`-참조된 파일을 읽고 요청된 형식으로 비평을 쓰면 끝이다.

## 요구사항

- **tmux** ≥ 3.0, tmux 세션 안에서 실행 중이어야 함.
- **Codex CLI** — 같은 tmux 윈도우의 sibling pane에서 실행 중. **default mode** (Plan mode 아님 — Codex가 파일을 써야 함). 윈도우당 Codex pane 1개. 2개 이상이면 대화식 선택기가 뜬다.
- **Python 3.13+** on `PATH`. (3.14에서 테스트.)
- **Claude Code** — Bash 툴 사용 가능해야 함. 오케스트레이션은 file-poll(`wait` 서브커맨드)로 동기 진행한다.

개발 시:

- `pytest` ≥ 8.4 (테스트 실행 용)

## 설치

```bash
# 클론 (또는 이미 클론된 상태)
cd ~/Documents/workspace/critique-loop

# Claude Code의 skills 디렉토리에 심볼릭 링크 → 스킬 자동 등록
mkdir -p ~/.claude/skills
ln -s "$PWD" ~/.claude/skills/critique-loop

# CLI 동작 확인
python3 ./critique_loop.py --help
```

Claude Code를 재시작하거나 새 세션을 열면 스킬이 인식된다.

## 사용법

tmux 세션 안에서 Claude Code를 실행 중이고, sibling pane에 Codex CLI가 있을 때:

```text
/critique-loop                           # 직전 Claude 메시지/제안 리뷰
/critique-loop src/foo.py                # 파일 리뷰
/critique-loop --diff                    # 현 브랜치의 diff vs base 리뷰
/critique-loop "sqlite 캐시 쓰자"          # 자유 텍스트 리뷰
```

주요 플래그:

```text
--rounds N            최대 라운드 수 오버라이드 (1..10, 기본 3)
--codex-pane %23      Codex pane이 여러 개일 때 명시
--no-health           ⚠ round-0 health check 생략 (디버그 전용; 보통 쓰지 않음)
--resume <run_id>     중단된 run 재개
--health              round-0 PONG 핸드셰이크만 단독 실행
--list                최근 run_id 목록 출력
--show <run_id>       과거 run의 합성 보고서 재출력
```

### 전형적인 세션

1. tmux 열기. Pane A: `claude`. Pane B: `codex`. 같은 윈도우.
2. Pane A에서: `/critique-loop src/worker/pool.py --rounds 3`
3. Codex pane 관찰: `@run-…/prompt-r1.md`를 받아 `critique-r1.md`를 쓰고 idle 상태로 돌아옴.
4. Claude가 ~60초 후 깨어나 비평을 읽고 계속 진행할지 결정.
5. 최대 3 라운드 후 (또는 Codex가 `VERDICT: done`을 내리면 일찍) Claude가 Pane A에 합성 보고서를 출력.

산출물은 `~/.claude/cache/critique-loop/<run_id>/`에 저장. `/critique-loop --show <run_id>`로 다시 출력 가능.

## v0.1.0 범위 메모

이 릴리즈는 **[SPEC.md](./SPEC.md)의 의도적 축소판** — 아직 실패 사례가 없는 기능에 복잡도를 쓰지 않고, 루프 전체를 end-to-end로 dogfood하는 것이 목표.

v0.1.0이 포함한 것:

- 파일 source-of-truth + `tmux paste-buffer` wake 채널
- Round-0 health check (PONG 핸드셰이크)
- `VERDICT: done` 시 조기 종료가 있는 N-round 루프
- Pane discovery (1개 자동 선택, 0개/복수 에러)
- argv 기반 `tmux` 호출 + payload allowlist

v0.1.0이 뺀 것 (SPEC.md 대비):

- `manifest.json` v2 스키마 → 단순화된 `state.json`
- 원자적 `.tmp` + rename + `.done` sentinel 프로토콜 → 단순 파일 쓰기
- 6-field 구조화 critique JSON → `VERDICT: continue|done`으로 끝나는 자유형 마크다운
- Finding별 accept/reject/defer 상태 머신
- `events.jsonl` 이벤트 로그
- `request_id` nonce 검증 (지연/중복 신호 드롭)
- Push 전 pane idle check
- Schema-repair retry

dogfooding에서 실제 실패 모드가 나오면 그때 단계적으로 도입. 그 전까지는 동작이 SPEC.md와 다르면 **`critique_loop.py`를 기준**으로 삼는다.

## 파일 구조

```text
critique-loop/
├── SKILL.md            # Claude가 읽는 오케스트레이션 절차
├── critique_loop.py    # Claude가 호출하는 CLI
├── test_critique_loop.py
├── pyproject.toml
├── SPEC.md             # 설계 문서 (목표 형태; v0.1.0은 부분집합)
├── SMOKE.md            # live Codex pane 대상 수동 체크리스트
├── HANDOFF.md          # 세션 인계 메모
├── README.md
└── docs/eng/           # 영어 원본 문서
```

## 개발

```bash
# 테스트 실행
python3 -m pytest -v

# Live Codex pane 대상 수동 smoke test
# SMOKE.md 참고
```

## 라이선스

미지정 — 개인용 스킬
