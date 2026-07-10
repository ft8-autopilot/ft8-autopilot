#!/usr/bin/env bash
# Sync ft8-autopilot → ft8-autopilot-publish (public tree, no personal data).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${SYNC_SRC:-$HOME/ai/cw-discover-opt}"
DST="$ROOT"

if [[ ! -d "$SRC/cw_discover" ]]; then
  echo "Source not found: $SRC"
  exit 1
fi

RSYNC_EX=(
  --archive --delete
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude '.pytest_cache/'
  --exclude '.hypothesis/'
  --exclude 'forgalminaplo/'
  --exclude 'logs/'
  --exclude 'state/'
  --exclude 'call_grid_cache.json'
  --exclude 'station_catalog.json'
  --exclude 'antenna_history.json'
  --exclude 'experiments/'
  --exclude 'ft8_protocol_rules.json'
  --exclude 'ft8_pro_decoder_research.json'
  --exclude 'bench_results.json'
  --exclude 'LOOP_LOG.md'
  --exclude 'OPT_SUMMARY.md'
  --exclude 'OPT_HUB.md'
)

echo "Sync $SRC → $DST"
rsync "${RSYNC_EX[@]}" "$SRC/cw_discover/" "$DST/cw_discover/"
rsync "${RSYNC_EX[@]}" "$SRC/tests/" "$DST/tests/"
rsync "${RSYNC_EX[@]}" \
  --exclude 'publish_to_github.sh' \
  --exclude 'sync_from_dev.sh' \
  --exclude 'sanitize_public_tree.py' \
  "$SRC/scripts/" "$DST/scripts/"
# data/: merge only — keep bundled docs/shapefile when dev tree has no data/
rsync --archive \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'call_grid_cache.json' \
  --exclude 'station_catalog.json' \
  --exclude 'antenna_history.json' \
  "$SRC/data/" "$DST/data/" 2>/dev/null || true
rsync "${RSYNC_EX[@]}" "$SRC/opt-lab/" "$DST/opt-lab/"
rsync "${RSYNC_EX[@]}" "$SRC/firmware/" "$DST/firmware/"

# Root launcher + dependencies (FT8-only tree)
for f in start start.txt requirements.txt VERSION; do
  if [[ -f "$SRC/$f" ]]; then
    cp -a "$SRC/$f" "$DST/$f"
  fi
done
if [[ -f "$SRC/requirements-ft8.txt" ]]; then
  cp -a "$SRC/requirements-ft8.txt" "$DST/"
fi
chmod +x "$DST/start" 2>/dev/null || true

# Keep publish-only scripts
cp -a "$DST/scripts/publish_to_github.sh" "$DST/scripts/publish_to_github.sh" 2>/dev/null || true

python3 "$DST/scripts/sanitize_public_tree.py" "$DST"

echo "Sanitized public tree at $DST"
