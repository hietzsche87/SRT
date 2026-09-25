#!/usr/bin/env bash
# 봇 코드(이 저장소)나 korail-mobile-api(main)에 새 커밋이 있으면 받아서 다시 빌드·재시작합니다.
# 새 버전이 제대로 뜨지 않으면 이전 이미지로 되돌리고, 같은 조합은 다시 시도하지 않습니다.
# 결과는 .env 의 TELEGRAM_BOT_TOKEN 으로 ALLOWED_USER_IDS 첫 사용자에게 알립니다.
#
# cron 이 root 로 실행합니다 (tools/install_auto_update.sh 참고). 수동 실행: sudo tools/auto_update.sh
#
# 스크립트 전체를 { } 로 감싸 둡니다 — git pull 이 실행 도중 이 파일을 바꿔도 bash 가 이미 다 읽은 뒤라 안전합니다.
{
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_OWNER="$(stat -c %U "$REPO_DIR")"
LIB_REPO="https://github.com/yakisoba0728/korail-mobile-api"
IMAGE="korail-telegram-bot"
CONTAINER="korail-bot"
STATE_DIR="/var/lib/korail-bot-update"
LOCK_FILE="/run/korail-bot-update.lock"

log() { echo "$(date '+%F %T') $*"; }

as_owner() { runuser -u "$REPO_OWNER" -- "$@"; }

env_value() {
    grep -E "^$1=" "$REPO_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- | sed -e "s/^['\"]//" -e "s/['\"]$//"
}

notify() {
    local token chat
    token="$(env_value TELEGRAM_BOT_TOKEN)"
    chat="$(env_value ALLOWED_USER_IDS | cut -d, -f1 | tr -d ' ')"
    [ -n "$token" ] && [ -n "$chat" ] || return 0
    curl -fsS -m 15 "https://api.telegram.org/bot${token}/sendMessage" \
        --data-urlencode "chat_id=${chat}" --data-urlencode "text=$1" >/dev/null || log "텔레그램 알림 실패"
}

healthy() {
    # 새 컨테이너가 재시작 없이 떠 있고, 봇이 시작 로그를 남겼는지 확인합니다.
    local state
    sleep 45
    state="$(docker inspect -f '{{.State.Running}} {{.RestartCount}}' "$CONTAINER" 2>/dev/null)"
    [ "$state" = "true 0" ] || return 1
    docker logs --since 2m "$CONTAINER" 2>&1 | grep -q "Application started"
}

main() {
    exec 9>"$LOCK_FILE"
    flock -n 9 || { log "이미 실행 중 — 건너뜀"; return 0; }
    mkdir -p "$STATE_DIR"
    cd "$REPO_DIR" || return 1

    as_owner git fetch -q origin || { log "봇 저장소 fetch 실패"; return 1; }
    local upstream bot_new lib_new deployed failed
    upstream="$(as_owner git rev-parse --abbrev-ref '@{u}' 2>/dev/null)"
    bot_new="$(as_owner git rev-parse --verify -q "$upstream" 2>/dev/null)"
    [ -n "$upstream" ] && [ -n "$bot_new" ] || { log "추적 브랜치를 찾지 못함 — 중단"; return 1; }
    lib_new="$(as_owner git ls-remote "$LIB_REPO" refs/heads/main | cut -c1-40)"
    [ -n "$lib_new" ] || { log "라이브러리 최신 커밋 확인 실패"; return 1; }

    deployed="$(cat "$STATE_DIR/deployed" 2>/dev/null)"
    failed="$(cat "$STATE_DIR/failed" 2>/dev/null)"
    [ "$deployed" = "$bot_new $lib_new" ] && return 0
    if [ "$failed" = "$bot_new $lib_new" ]; then
        log "이 조합은 이미 실패함 — 새 커밋을 기다림 (bot ${bot_new:0:7}, lib ${lib_new:0:7})"
        return 0
    fi

    log "업데이트 시작: bot ${bot_new:0:7}, lib ${lib_new:0:7} (이전: ${deployed:-없음})"

    # 서버에서 직접 고친 파일이 있으면 지우지 않고 stash 에 보관합니다.
    if [ -n "$(as_owner git status --porcelain --untracked-files=no)" ]; then
        as_owner git stash push -q -m "auto-update $(date '+%F %T')" && log "로컬 수정을 git stash 에 보관함"
    fi
    if ! as_owner git merge -q --ff-only "$upstream"; then
        log "fast-forward 불가 — 중단"
        notify "⚠️ 봇 자동 업데이트 중단: 서버 코드가 GitHub 와 갈라졌습니다. 확인이 필요합니다."
        echo "$bot_new $lib_new" > "$STATE_DIR/failed"
        return 1
    fi

    docker image inspect "$IMAGE:latest" >/dev/null 2>&1 && docker tag "$IMAGE:latest" "$IMAGE:previous"

    if docker compose build -q && docker compose up -d && healthy; then
        echo "$bot_new $lib_new" > "$STATE_DIR/deployed"
        rm -f "$STATE_DIR/failed"
        log "업데이트 성공"
        notify "✅ 봇 자동 업데이트 완료
봇 ${bot_new:0:7} · 라이브러리 ${lib_new:0:7}"
        return 0
    fi

    log "새 버전이 정상 기동하지 않음 — 이전 이미지로 되돌림"
    local tail_log
    tail_log="$(docker logs --tail 15 "$CONTAINER" 2>&1 | tail -c 1500)"
    echo "$bot_new $lib_new" > "$STATE_DIR/failed"
    if docker image inspect "$IMAGE:previous" >/dev/null 2>&1; then
        docker tag "$IMAGE:previous" "$IMAGE:latest"
        docker compose up -d --no-build --force-recreate
    fi
    notify "❌ 봇 자동 업데이트 실패 — 이전 버전으로 되돌렸습니다.
봇 ${bot_new:0:7} · 라이브러리 ${lib_new:0:7}

마지막 로그:
${tail_log}"
    return 1
}

main "$@"
exit $?
}
