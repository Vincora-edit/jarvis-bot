#!/bin/bash
# ============================================
# JARVIS VPN - Скрипт установки Xray-core
# ============================================
# Запуск: bash install_xray.sh
#
# Что делает:
# 1. Устанавливает Xray-core
# 2. Генерирует x25519 ключи для Reality
# 3. Создаёт конфиг с VLESS + Reality
# 4. Настраивает systemd сервис
# 5. Включает API для управления пользователями

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}   JARVIS VPN - Установка Xray-core${NC}"
echo -e "${GREEN}============================================${NC}"

# Проверка root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Ошибка: запустите скрипт от root${NC}"
    exit 1
fi

# === ПАРАМЕТРЫ ===
XRAY_VERSION="1.8.7"  # Можно обновить на актуальную
XRAY_DIR="/usr/local/bin"
CONFIG_DIR="/etc/xray"
LOG_DIR="/var/log/xray"

# Порты
VLESS_PORT=${VLESS_PORT:-443}
API_PORT=${API_PORT:-10085}

# Reality SNI (какой сайт маскируем)
REALITY_SNI=${REALITY_SNI:-"www.google.com"}

echo ""
echo -e "${YELLOW}Параметры:${NC}"
echo "  VLESS порт: $VLESS_PORT"
echo "  API порт: $API_PORT"
echo "  Reality SNI: $REALITY_SNI"
echo ""

# === УСТАНОВКА XRAY ===
echo -e "${GREEN}[1/6] Установка Xray-core...${NC}"

# Создаём директории
mkdir -p $XRAY_DIR $CONFIG_DIR $LOG_DIR

# Скачиваем и устанавливаем
cd /tmp
ARCH=$(uname -m)
case $ARCH in
    x86_64) ARCH="64" ;;
    aarch64) ARCH="arm64-v8a" ;;
    *) echo "Неподдерживаемая архитектура: $ARCH"; exit 1 ;;
esac

wget -q "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-${ARCH}.zip" -O xray.zip
unzip -o xray.zip -d xray_temp
mv xray_temp/xray $XRAY_DIR/xray
chmod +x $XRAY_DIR/xray
rm -rf xray.zip xray_temp

echo -e "${GREEN}✓ Xray установлен: $($XRAY_DIR/xray version | head -1)${NC}"

# === ГЕНЕРАЦИЯ КЛЮЧЕЙ ===
echo -e "${GREEN}[2/6] Генерация Reality ключей...${NC}"

# Генерируем x25519 ключи
KEYS=$($XRAY_DIR/xray x25519)
PRIVATE_KEY=$(echo "$KEYS" | grep "Private" | awk '{print $3}')
PUBLIC_KEY=$(echo "$KEYS" | grep "Public" | awk '{print $3}')

# Генерируем Short ID (8 hex символов)
SHORT_ID=$(openssl rand -hex 4)

# Генерируем UUID для тестового пользователя
TEST_UUID=$(cat /proc/sys/kernel/random/uuid)

echo -e "${GREEN}✓ Ключи сгенерированы${NC}"

# === СОЗДАНИЕ КОНФИГА ===
echo -e "${GREEN}[3/6] Создание конфигурации...${NC}"

cat > $CONFIG_DIR/config.json << EOF
{
  "log": {
    "loglevel": "warning",
    "access": "$LOG_DIR/access.log",
    "error": "$LOG_DIR/error.log"
  },
  "api": {
    "tag": "api",
    "services": [
      "HandlerService",
      "StatsService"
    ]
  },
  "stats": {},
  "policy": {
    "levels": {
      "0": {
        "statsUserUplink": true,
        "statsUserDownlink": true
      }
    },
    "system": {
      "statsInboundUplink": true,
      "statsInboundDownlink": true,
      "statsOutboundUplink": true,
      "statsOutboundDownlink": true
    }
  },
  "inbounds": [
    {
      "tag": "api",
      "listen": "127.0.0.1",
      "port": $API_PORT,
      "protocol": "dokodemo-door",
      "settings": {
        "address": "127.0.0.1"
      }
    },
    {
      "tag": "vless-reality",
      "listen": "0.0.0.0",
      "port": $VLESS_PORT,
      "protocol": "vless",
      "settings": {
        "clients": [
          {
            "id": "$TEST_UUID",
            "email": "test@jarvis.vpn",
            "flow": "xtls-rprx-vision"
          }
        ],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "dest": "$REALITY_SNI:443",
          "xver": 0,
          "serverNames": [
            "$REALITY_SNI",
            "www.$REALITY_SNI"
          ],
          "privateKey": "$PRIVATE_KEY",
          "shortIds": [
            "$SHORT_ID"
          ]
        }
      },
      "sniffing": {
        "enabled": true,
        "destOverride": [
          "http",
          "tls",
          "quic"
        ]
      }
    }
  ],
  "outbounds": [
    {
      "tag": "direct",
      "protocol": "freedom"
    },
    {
      "tag": "blocked",
      "protocol": "blackhole"
    }
  ],
  "routing": {
    "rules": [
      {
        "type": "field",
        "inboundTag": ["api"],
        "outboundTag": "api"
      },
      {
        "type": "field",
        "outboundTag": "blocked",
        "ip": ["geoip:private"]
      },
      {
        "type": "field",
        "outboundTag": "direct",
        "network": "udp,tcp"
      }
    ]
  }
}
EOF

echo -e "${GREEN}✓ Конфиг создан: $CONFIG_DIR/config.json${NC}"

# === SYSTEMD СЕРВИС ===
echo -e "${GREEN}[4/6] Настройка systemd...${NC}"

cat > /etc/systemd/system/xray.service << EOF
[Unit]
Description=Xray Service
Documentation=https://github.com/xtls/xray-core
After=network.target nss-lookup.target

[Service]
Type=simple
User=root
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ExecStart=$XRAY_DIR/xray run -config $CONFIG_DIR/config.json
Restart=on-failure
RestartPreventExitStatus=23
LimitNPROC=10000
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable xray
systemctl restart xray

echo -e "${GREEN}✓ Systemd сервис настроен${NC}"

# === FIREWALL ===
echo -e "${GREEN}[5/6] Настройка firewall...${NC}"

if command -v ufw &> /dev/null; then
    ufw allow $VLESS_PORT/tcp
    echo -e "${GREEN}✓ UFW: открыт порт $VLESS_PORT${NC}"
elif command -v firewall-cmd &> /dev/null; then
    firewall-cmd --permanent --add-port=$VLESS_PORT/tcp
    firewall-cmd --reload
    echo -e "${GREEN}✓ Firewalld: открыт порт $VLESS_PORT${NC}"
else
    echo -e "${YELLOW}⚠ Firewall не найден, откройте порт $VLESS_PORT вручную${NC}"
fi

# === ПРОВЕРКА ===
echo -e "${GREEN}[6/6] Проверка...${NC}"

sleep 2

if systemctl is-active --quiet xray; then
    echo -e "${GREEN}✓ Xray запущен и работает${NC}"
else
    echo -e "${RED}✗ Ошибка запуска Xray${NC}"
    journalctl -u xray -n 20
    exit 1
fi

# === ВЫВОД ИНФОРМАЦИИ ===
SERVER_IP=$(curl -s ifconfig.me || hostname -I | awk '{print $1}')

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}   УСТАНОВКА ЗАВЕРШЕНА!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo -e "${YELLOW}Данные для .env (VPN_SERVERS):${NC}"
echo ""
echo "{"
echo "  \"id\": \"$(hostname | tr '[:upper:]' '[:lower:]' | tr -d ' ')\","
echo "  \"name\": \"VPN Server\","
echo "  \"location\": \"Unknown\","
echo "  \"host\": \"$SERVER_IP\","
echo "  \"ssh_port\": 22,"
echo "  \"ssh_user\": \"root\","
echo "  \"ssh_password\": \"YOUR_SSH_PASSWORD\","
echo "  \"xray_api_port\": $API_PORT,"
echo "  \"inbound_port\": $VLESS_PORT,"
echo "  \"inbound_tag\": \"vless-reality\","
echo "  \"reality_private_key\": \"$PRIVATE_KEY\","
echo "  \"reality_public_key\": \"$PUBLIC_KEY\","
echo "  \"reality_short_id\": \"$SHORT_ID\","
echo "  \"reality_server_name\": \"$REALITY_SNI\","
echo "  \"priority\": 10,"
echo "  \"max_users\": 1000"
echo "}"
echo ""
echo -e "${YELLOW}Тестовый VLESS URL:${NC}"
echo "vless://$TEST_UUID@$SERVER_IP:$VLESS_PORT?type=tcp&security=reality&pbk=$PUBLIC_KEY&fp=chrome&sni=$REALITY_SNI&sid=$SHORT_ID&flow=xtls-rprx-vision#JarvisVPN"
echo ""
echo -e "${GREEN}Сохраните эти данные!${NC}"
echo ""
echo "Команды:"
echo "  Статус: systemctl status xray"
echo "  Логи:   journalctl -u xray -f"
echo "  Рестарт: systemctl restart xray"
echo ""
