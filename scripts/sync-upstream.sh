#!/usr/bin/env bash
# Hermes upstream sync — fork(`chrisryugj/hermes-agent`) 를 origin(`NousResearch/hermes-agent`)
# 의 main 과 동기화한다.
#
# 사용:
#   bash scripts/sync-upstream.sh              # 기본: merge (history 보존, 충돌 한 번에)
#   bash scripts/sync-upstream.sh rebase       # rebase (linear history, 충돌이 commit 별로 분산)
#   bash scripts/sync-upstream.sh dry          # fetch + behind/ahead 카운트만
#
# 안전장치:
#   - 실행 직전 HEAD 를 refs/backups/<branch>-<timestamp> 로 백업.
#   - 작업 트리/인덱스에 미커밋 변경이 있으면 거부 (stash 강제 안 함).
#   - 작업 중 충돌 시 사용자에게 핸들오버 — 자동 abort 안 함.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

MODE="${1:-merge}"

# ─── 사전 점검 ─────────────────────────────────────────────
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "✗ 미커밋 변경이 있습니다. commit 또는 stash 후 재시도하세요." >&2
    git status -s
    exit 1
fi

CURRENT="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT" == "HEAD" ]]; then
    echo "✗ detached HEAD 상태입니다. 브랜치로 전환 후 재시도하세요." >&2
    exit 1
fi

# ─── fetch ─────────────────────────────────────────────────
echo "==> fetch origin (upstream)"
git fetch origin --prune --tags
if git remote | grep -q '^fork$'; then
    echo "==> fetch fork"
    git fetch fork --prune --tags
fi

BEHIND=$(git rev-list --count HEAD..origin/main)
AHEAD=$(git rev-list --count origin/main..HEAD)

echo
echo "현재 브랜치: $CURRENT"
echo "behind origin/main: $BEHIND commits"
echo "ahead  origin/main: $AHEAD commits"
echo

if [[ "$MODE" == "dry" ]]; then
    if (( BEHIND > 0 )); then
        echo "==> upstream 변경 미리보기 (최근 20):"
        git log --oneline HEAD..origin/main | head -20
    fi
    exit 0
fi

if (( BEHIND == 0 )); then
    echo "✓ 이미 origin/main 과 동기화되어 있습니다."
    exit 0
fi

# ─── 백업 ──────────────────────────────────────────────────
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_REF="refs/backups/${CURRENT}-${TS}"
git update-ref "$BACKUP_REF" HEAD
echo "✓ 백업 ref: $BACKUP_REF -> $(git rev-parse --short HEAD)"
echo "  되돌리려면: git reset --hard $BACKUP_REF"
echo

# ─── 동기화 실행 ───────────────────────────────────────────
case "$MODE" in
    merge)
        echo "==> merge origin/main into $CURRENT"
        git merge --no-edit origin/main || {
            echo
            echo "✗ merge 충돌 발생. 충돌 해결 후 \`git commit\` 또는 \`git merge --abort\`."
            exit 2
        }
        ;;
    rebase)
        echo "==> rebase $CURRENT onto origin/main"
        git rebase origin/main || {
            echo
            echo "✗ rebase 충돌 발생. 해결 후 \`git rebase --continue\`, 포기하면 \`git rebase --abort\`."
            exit 2
        }
        ;;
    *)
        echo "사용법: $0 [merge|rebase|dry]" >&2
        exit 1
        ;;
esac

echo
echo "✓ 동기화 완료. fork 에 반영하려면:"
if [[ "$MODE" == "rebase" ]]; then
    echo "    git push fork $CURRENT --force-with-lease"
else
    echo "    git push fork $CURRENT"
fi
