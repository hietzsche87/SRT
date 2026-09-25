#!/usr/bin/env bash
# 자동 업데이트를 켜거나 끕니다.
#   켜기: sudo bash tools/install_auto_update.sh
#   끄기: sudo bash tools/install_auto_update.sh --remove
set -euo pipefail

CRON_FILE="/etc/cron.d/korail-bot-update"
LOG_FILE="/var/log/korail-bot-update.log"
SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/auto_update.sh"

if [ "$(id -u)" -ne 0 ]; then
    echo "sudo 로 실행하세요: sudo bash $0 $*" >&2
    exit 1
fi

if [ "${1:-}" = "--remove" ]; then
    rm -f "$CRON_FILE"
    echo "자동 업데이트를 껐습니다."
    exit 0
fi

chmod +x "$SCRIPT"
cat > "$CRON_FILE" <<EOF
# 봇 자동 업데이트 (10분마다). 끄기: sudo bash $(dirname "$SCRIPT")/install_auto_update.sh --remove
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/10 * * * * root $SCRIPT >> $LOG_FILE 2>&1
EOF
chmod 644 "$CRON_FILE"
touch "$LOG_FILE"
echo "자동 업데이트를 켰습니다 (10분마다). 로그: $LOG_FILE"
echo "지금 한 번 실행합니다…"
"$SCRIPT" | tee -a "$LOG_FILE"
