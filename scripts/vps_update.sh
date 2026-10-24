#!/bin/sh
# Deploys the latest code on the VPS (README "Run it on a DigitalOcean droplet").
# Run on the server:   sh scripts/vps_update.sh
# Pulls the checked-out branch, rebuilds the image, runs migrate (role, migrations,
# salary check) and only then restarts web, as on Railway (DECISIONS H2).
set -e
cd "$(dirname "$0")/.."

compose="docker compose -f docker-compose.prod.yml"

git pull --ff-only
$compose build
# Explicitly, and before web restarts: `up` alone does not rerun a migrate that already finished.
$compose run --rm migrate
$compose up -d
docker image prune -f >/dev/null
$compose ps
