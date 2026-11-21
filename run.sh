#!/usr/bin/env sh
# Full local run on Linux or macOS: start the stack, then run every stage in the pipeline container.
# Needs Docker. CDSE_USERNAME and CDSE_PASSWORD must be set for the Copernicus downloads.
set -eu

docker compose up -d --build
run() { docker compose exec -T pipeline msp "$@"; }

run init-db
run prefect-setup
run s1-select
run reference-layers
run metocean
run backfill                 # replays the study period day by day through every flow
run tune-detector            # development passes only
run redetect
run evaluate
run ml train
run ml classify
run quality
run catalogue
run demo-export
run figures
run readme-tables
run scan
echo "done: open http://localhost:4200 for the Prefect run history"
