# Fork Maintenance

이 fork(`chrisryugj/hermes-agent`)는 `NousResearch/hermes-agent` 를 upstream(`origin`) 으로 두고
chris 환경 전용 변경(웹 대시보드, 배포 아티팩트, 한국 법령 MCP 가이던스 등) 을 얹어 운영한다.

## Remote 구성

```
origin   https://github.com/NousResearch/hermes-agent.git   (upstream — 절대 push 금지)
fork     https://github.com/chrisryugj/hermes-agent.git     (개인 fork — push 대상)
```

## Fork-only 변경 영역

| 영역 | 위치 | 설명 |
|------|------|------|
| 웹 대시보드 | `dashboard/` | gateway 관리 UI (Chat 탭 포함). DASHBOARD_DIR 은 self-resolve |
| Dashboard wire-up | `gateway/platforms/api_server.py` | `Dashboard plugin` 블록 (HERMES_DASHBOARD_DIR override 가능) |
| FC-RAG 로그 엔드포인트 | `gateway/platforms/api_server.py::_handle_fc_rag_log` | `/api/fc-rag-log` POST 수신 → `~/.hermes/logs/fc-rag-queries.jsonl` |
| skip_context_files | `gateway/platforms/api_server.py`, `run_agent.py` | API 파라미터 — SOUL.md 등 컨텍스트 파일 차단 |
| 한국 법령 MCP 가이던스 | `agent/prompt_builder.py::KOREAN_LAW_MCP_GUIDANCE` + `run_agent.py` 주입 로직 | korean-law MCP 로드 시 system prompt 에 자동 포함 |
| 배포 아티팩트 | `packaging/launchagents/`, `scripts/run-tunnel.sh`, `scripts/start-hermes-tunnel.sh`, `scripts/sync-cloudflare-worker.sh`, `dashboard/hermes-monitor-mac.py` | macOS 와치독 + CF 터널 + 모니터 |

위 영역 외 파일은 가급적 fork 에서 직접 수정하지 않는다 — 충돌 면적을 최소화하여 upstream sync 비용을 낮춘다.

## Upstream 동기화 절차

매주~격주로 다음 명령 실행:

```bash
# 1) 미리보기만 (현재 얼마나 밀려있는지)
bash scripts/sync-upstream.sh dry

# 2) 실제 동기화 — merge 권장 (history 보존)
bash scripts/sync-upstream.sh

# 3) 충돌 발생 시 해결 후
git commit                  # merge 모드
# 또는
git rebase --continue       # rebase 모드

# 4) 검증 — 핵심 파일 syntax + import
python3 -c "import ast; ast.parse(open('gateway/platforms/api_server.py').read())"
python3 -c "import ast; ast.parse(open('run_agent.py').read())"

# 5) fork 에 push
git push fork <current-branch>
# rebase 한 경우엔
git push fork <current-branch> --force-with-lease
```

스크립트는 실행 직전 `refs/backups/<branch>-<timestamp>` 로 HEAD 백업을 만든다.
원위치 복귀: `git reset --hard refs/backups/<branch>-<timestamp>`.

## 충돌 빈발 지점

`gateway/platforms/api_server.py` — fork 의 dashboard wire-up 블록과 skip_context_files 매개변수 전파 라인이
upstream 의 신규 라우트/매개변수 추가와 자주 부딪친다. 충돌이 보이면 보통 **둘 다 살리는 방향**으로 해결.

## 대규모 누락 후 재정렬

밀린 commit 이 1,000+ 건 누적되어 단순 merge 로 처리 불가능할 때:

1. 현재 브랜치를 `refs/backups/...` 로 백업
2. `git switch -c <new-branch> origin/main`
3. fork-only commit 들을 cherry-pick (대부분 dashboard·deployment 영역이라 충돌 적음)
4. 검증 후 `git branch -f <main-branch> <new-branch> && git push fork <main-branch> --force-with-lease`

이번 2026-05-09 정렬도 이 절차로 수행됨 (3,807 commits behind → 깨끗이 해소).
