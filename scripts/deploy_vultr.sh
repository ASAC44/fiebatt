#!/bin/bash
# deploy fiebatt to a vultr VPS
# usage: ./scripts/deploy_vultr.sh <server-ip>

set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

SERVER=$1

if [ -z "$SERVER" ]; then
  echo "usage: ./deploy.sh <server-ip>"
  exit 1
fi

echo "deploying fiebatt to $SERVER..."

# sync repo to server
rsync -avz --exclude node_modules --exclude .venv --exclude storage --exclude .git \
  "$ROOT_DIR/" root@$SERVER:/opt/fiebatt/

# build and start on server
ssh root@$SERVER << 'EOF'
  cd /opt/fiebatt
  docker compose down
  docker compose build
  docker compose up -d
  echo "fiebatt is live"
EOF

echo "done. fiebatt is running at http://$SERVER"
