# critique-loop

같은 tmux 윈도우의 Claude Code pane과 Codex CLI pane 간 적대적 코드 리뷰 루프.

Claude가 프롬프트 파일을 쓰고 `tmux paste-buffer`로 Codex를 깨우면, Codex가 비평 파일을 쓴다. file-poll로 완료를 감지해서 N 라운드 반복하고 합성 보고서를 출력한다.

## 요구사항

- tmux 안에서 실행 중
- 같은 윈도우의 sibling pane에 Codex CLI (default mode, Plan mode 아님)
- Python 3.13+
- Claude Code (Bash 툴 사용 가능)

## 설치

```bash
mkdir -p ~/.claude/skills
ln -s "$PWD" ~/.claude/skills/critique-loop
```

Claude Code 재시작 후 스킬 인식.

## 사용

```text
/critique-loop                    # 직전 메시지 리뷰
/critique-loop src/foo.py         # 파일 리뷰
/critique-loop --diff             # 현재 브랜치 diff 리뷰
/critique-loop "텍스트"            # 자유 텍스트 리뷰
/critique-loop --rounds N <input> # 라운드 수 (기본 3, 최대 10)
/critique-loop --resume <run_id>  # 중단된 run 재개
/critique-loop --list             # 과거 run 목록
```

산출물: `~/.claude/cache/critique-loop/<run_id>/`

**캐시 정책:** 매 호출마다 새 run 디렉토리가 생기고, **최신 10개만 자동 유지**된다 (11번째 호출 시 가장 오래된 게 silent 삭제). 보존이 필요한 리뷰는 캐시 밖으로 복사할 것. 전체 reset은 `python3 critique_loop.py clean`.

상세 절차는 `SKILL.md`. CLI 도움말은 `python3 critique_loop.py --help`.

## 개발

```bash
python3 -m pytest -v
```
