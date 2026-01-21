#!/bin/bash
# ==============================================
# Jarvis Bot - Deploy Script
# ==============================================

# Загружаем переменные из .env
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Серверы (из .env)
BOT_SERVER="${BOT_SERVER:-147.45.187.16}"
BOT_USER="${BOT_SERVER_USER:-root}"
BOT_PASS="${BOT_SERVER_PASS}"

VPN_SERVER="${VPN_SERVER:-72.56.88.242}"
VPN_USER="${VPN_SERVER_USER:-root}"
VPN_PASS="${VPN_SERVER_PASS}"

# Проверка что пароли заданы
if [ -z "$BOT_PASS" ]; then
    echo "❌ Ошибка: BOT_SERVER_PASS не задан в .env"
    exit 1
fi

# Пути на серверах
BOT_PATH="/opt/jarvis-bot"
ADMIN_PATH="/opt/admin-panel"
BOT_DB="/opt/jarvis-bot/bot_database.db"

# SSH опции
SSH_OPTS="-o StrictHostKeyChecking=no"

# ==============================================
# Функции
# ==============================================

# Деплой бота
deploy_bot() {
    echo "🚀 Deploying bot to $BOT_SERVER..."

    # Синхронизация файлов (исключаем venv, __pycache__, .git)
    sshpass -p "$BOT_PASS" rsync -avz --progress \
        --exclude 'venv/' \
        --exclude '__pycache__/' \
        --exclude '*.pyc' \
        --exclude '.git/' \
        --exclude '.env' \
        --exclude '*.db' \
        --exclude 'deploy.sh' \
        ./ $BOT_USER@$BOT_SERVER:$BOT_PATH/

    # Перезапуск сервиса
    sshpass -p "$BOT_PASS" ssh $SSH_OPTS $BOT_USER@$BOT_SERVER "systemctl restart jarvis-bot"

    echo "✅ Bot deployed!"
}

# Деплой админки
deploy_admin() {
    echo "🚀 Deploying admin panel to $BOT_SERVER..."

    # Копируем админку
    sshpass -p "$BOT_PASS" scp $SSH_OPTS admin-panel/main.py $BOT_USER@$BOT_SERVER:$ADMIN_PATH/main.py

    # Перезапуск сервиса
    sshpass -p "$BOT_PASS" ssh $SSH_OPTS $BOT_USER@$BOT_SERVER "systemctl restart admin-panel"

    echo "✅ Admin panel deployed!"
}

# Статус сервисов
status() {
    echo "📊 Services status on $BOT_SERVER:"
    sshpass -p "$BOT_PASS" ssh $SSH_OPTS $BOT_USER@$BOT_SERVER "systemctl status jarvis-bot --no-pager -l | head -20"
    echo ""
    sshpass -p "$BOT_PASS" ssh $SSH_OPTS $BOT_USER@$BOT_SERVER "systemctl status admin-panel --no-pager -l | head -20"
}

# Логи бота
logs_bot() {
    echo "📜 Bot logs:"
    sshpass -p "$BOT_PASS" ssh $SSH_OPTS $BOT_USER@$BOT_SERVER "journalctl -u jarvis-bot -n ${1:-50} --no-pager"
}

# Логи админки
logs_admin() {
    echo "📜 Admin panel logs:"
    sshpass -p "$BOT_PASS" ssh $SSH_OPTS $BOT_USER@$BOT_SERVER "journalctl -u admin-panel -n ${1:-50} --no-pager"
}

# Миграция БД
migrate() {
    echo "🔄 Running migration on $BOT_DB..."
    sshpass -p "$BOT_PASS" ssh $SSH_OPTS $BOT_USER@$BOT_SERVER "python3 << 'MIGRATION'
import sqlite3
conn = sqlite3.connect('$BOT_DB')
cursor = conn.cursor()

# Добавить миграции здесь
# cursor.execute('ALTER TABLE ...')

conn.commit()
conn.close()
print('Migration complete!')
MIGRATION"
}

# SSH на бот-сервер
ssh_bot() {
    echo "🔌 Connecting to bot server..."
    sshpass -p "$BOT_PASS" ssh $SSH_OPTS $BOT_USER@$BOT_SERVER
}

# SSH на VPN-сервер
ssh_vpn() {
    echo "🔌 Connecting to VPN server..."
    sshpass -p "$VPN_PASS" ssh $SSH_OPTS $VPN_USER@$VPN_SERVER
}

# Полный деплой
deploy_all() {
    deploy_bot
    deploy_admin
    status
}

# Выполнить команду на сервере
run_cmd() {
    sshpass -p "$BOT_PASS" ssh $SSH_OPTS $BOT_USER@$BOT_SERVER "$@"
}

# ==============================================
# Команды
# ==============================================

case "$1" in
    bot)
        deploy_bot
        ;;
    admin)
        deploy_admin
        ;;
    all)
        deploy_all
        ;;
    status)
        status
        ;;
    logs)
        logs_bot ${2:-50}
        ;;
    logs-admin)
        logs_admin ${2:-50}
        ;;
    migrate)
        migrate
        ;;
    ssh)
        ssh_bot
        ;;
    ssh-vpn)
        ssh_vpn
        ;;
    restart)
        sshpass -p "$BOT_PASS" ssh $SSH_OPTS $BOT_USER@$BOT_SERVER "systemctl restart jarvis-bot && systemctl restart admin-panel"
        echo "✅ Services restarted"
        ;;
    cmd)
        shift
        run_cmd "$@"
        ;;
    *)
        echo "Jarvis Deploy Script"
        echo ""
        echo "Usage: ./deploy.sh <command>"
        echo ""
        echo "Commands:"
        echo "  bot        - Deploy bot only"
        echo "  admin      - Deploy admin panel only"
        echo "  all        - Deploy everything"
        echo "  status     - Show services status"
        echo "  logs [n]   - Show bot logs (default: 50 lines)"
        echo "  logs-admin - Show admin panel logs"
        echo "  migrate    - Run DB migration"
        echo "  ssh        - SSH to bot server"
        echo "  ssh-vpn    - SSH to VPN server"
        echo "  restart    - Restart all services"
        echo "  cmd <...>  - Run command on bot server"
        echo ""
        echo "Servers:"
        echo "  Bot:   $BOT_USER@$BOT_SERVER"
        echo "  VPN:   $VPN_USER@$VPN_SERVER"
        ;;
esac
