#!/bin/sh
# Deploys the latest code on the VPS (README "Run it on a DigitalOcean droplet").
# Run on the server:   sh scripts/vps_update.sh
# Pulls the checked-out branch, rebuilds the image, runs migrate (role, migrations,
# salary check) and only then restarts web, as on Railway (DECISIONS H2).
set -e
cd "$(dirname "$0")/.."

compose="docker compose -f docker-compose.prod.yml"

before=$(git rev-parse HEAD)
git pull --ff-only
# sh keeps running the copy of this file it already loaded, so a pull that changed this script would
# finish with the old steps. Start again once with the new copy (the second pull finds nothing new).
if [ -z "$VPS_UPDATE_RESTARTED" ] && ! git diff --quiet "$before" HEAD -- scripts/vps_update.sh; then
    echo "scripts/vps_update.sh changed: running the new version"
    VPS_UPDATE_RESTARTED=1 exec sh scripts/vps_update.sh
fi
# The footer's version number, worked out from the commit messages since the last release (H9).
python3 scripts/version.py --write >/dev/null
# The exact commit behind it: shown when hovering over it.
git log -1 --date=format:%Y-%m-%d --format='%h, %cd' > BUILD
$compose build
# Explicitly, and before web restarts: `up` alone does not rerun a migrate that already finished.
$compose run --rm migrate
$compose up -d
docker image prune -f >/dev/null
$compose ps
echo "Deployed v$(cat RELEASE) (build $(cat BUILD))"
