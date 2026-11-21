#!/usr/bin/env sh
# Build the website and package it with the small FastAPI server as a zip for App Service.
set -eu
cd "$(dirname "$0")/.."
(cd web && npm ci && npm run build)
rm -rf build/site && mkdir -p build/site
cp server/app.py server/requirements.txt build/site/
cp -r web/dist build/site/dist
(cd build/site && python -c "import shutil; shutil.make_archive('../site', 'zip', '.')")
echo "package: build/site.zip"
