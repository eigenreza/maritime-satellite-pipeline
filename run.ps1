# Full local run on Windows (PowerShell): start the stack, then run every stage in the pipeline container.
# Needs Docker Desktop. CDSE_USERNAME and CDSE_PASSWORD must be set for the Copernicus downloads.
$ErrorActionPreference = "Stop"

docker compose up -d --build
function Run-Msp { docker compose exec -T pipeline msp @args; if ($LASTEXITCODE -ne 0) { throw "msp $args failed" } }

Run-Msp init-db
Run-Msp prefect-setup
Run-Msp s1-select
Run-Msp reference-layers
Run-Msp metocean
Run-Msp backfill
Run-Msp tune-detector
Run-Msp redetect
Run-Msp evaluate
Run-Msp ml train
Run-Msp ml classify
Run-Msp quality
Run-Msp catalogue
Run-Msp demo-export
Run-Msp figures
Run-Msp readme-tables
Run-Msp scan
Write-Host "done: open http://localhost:4200 for the Prefect run history"
