# critique-with-codex

코드를 짜기 전에 설계 문서, 아키텍처 계획, 스펙을 Codex와 함께 반복 정제하는 도구.

Codex가 plan을 비평 → Claude가 반영해 새 버전 작성 → 사용자 승인 → 다음 라운드. 아티팩트가 라운드를 거치며 진화한다. 코드 diff 리뷰가 목적이라면 `/codex`를 사용할 것.

## 요구사항

- tmux 안에서 실행 중
- 같은 윈도우의 sibling pane에 Codex CLI (default mode, Plan mode 아님)
- Python 3.13+
- Claude Code (Bash 툴 사용 가능)

## 설치

```bash
mkdir -p ~/.claude/skills
ln -s "$PWD" ~/.claude/skills/critique-with-codex
```

Claude Code 재시작 후 스킬 인식.

## 사용

```text
/critique-with-codex plan.md             # 설계 문서 정제 (인터랙티브, 기본)
/critique-with-codex spec.md             # 스펙 정제
/critique-with-codex "아키텍처 제안 텍스트"  # 자유 텍스트 정제
/critique-with-codex --auto plan.md      # 비대화형 자동 모드 (체크포인트 생략)
/critique-with-codex --auto --rounds N plan.md  # --auto 전용 max_rounds (기본 3, 최대 10)
/critique-with-codex --resume <run_id>   # 중단된 run 재개
/critique-with-codex --list              # 과거 run 목록
```

## 라운드 흐름

```
plan-v1.md (원본)
  ↓ Codex critique
Claude가 plan-v{N+1}.draft.md 작성
  ↓
[인터랙티브] 사용자에게 결과 표시
  ↓ 사용자 응답
  - 승인 → plan-v{N+1}.md 확정, 다음 라운드
  - 수정 요청 → draft 재수정 후 재표시
  - stop → 마지막 승인본으로 종료
  ↓
[--auto] 자동 승인, 체크포인트 없음
```

**승인 신호**: "응", "ok", "approve", "좋아", 빈 응답 등 자연어로 판단. 명시적으로는 `approve` 또는 `승인`.  
**종료 신호**: "stop", "그만", "종료".  
**수정 요청**: 그 외 모든 텍스트.  
**모호한 응답**: Claude가 한 번 재질문.

**`--auto` 주의**: 체크포인트를 생략하지만, Claude의 plan 수정은 그대로 수행됨. v0.1(artifact 불변)과 다름.

## 산출물

`~/.claude/cache/critique-with-codex/<run_id>/`

```
plan-v1.md          # 원본
plan-v2.md          # Round 1 후 승인본
plan-v3.md          # Round 2 후 승인본
critique-r1.md      # Codex 비평
critique-r2.md
...
```

**캐시 정책:** 최신 10개 run만 자동 유지. 11번째 호출 시 가장 오래된 것 silent 삭제. 보존 필요한 run은 캐시 밖으로 복사. 전체 reset: `python3 critique_with_codex.py clean`.

상세 절차는 `SKILL.md`. CLI 도움말: `python3 critique_with_codex.py --help`.

## 개발

```bash
uv run pytest -v
```
