#!/bin/bash
# deploy fiebatt to a vultr VPS
# usage: ./deploy.sh <server-ip>

set -e

SERVER=$1

if [ -z "$SERVER" ]; then
  echo "usage: ./deploy.sh <server-ip>"
  exit 1
fi

echo "deploying fiebatt to $SERVER..."

# sync repo to server
rsync -avz --exclude node_modules --exclude .venv --exclude storage --exclude .git \
  ../ root@$SERVER:/opt/fiebatt/

# build and start on server
ssh root@$SERVER << 'EOF'
  cd /opt/fiebatt/infra
  docker compose down
  docker compose build
  docker compose up -d
  echo "fiebatt is live"
EOF

echo "done. fiebatt is running at http://$SERVER"
