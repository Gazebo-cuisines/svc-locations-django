# Hard-restart local Django so stale StatReloader processes cannot serve old URLs.
# Usage (from repo root):  .\scripts\dev.ps1
# Optional: .\scripts\dev.ps1 -Port 8000

param(
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$python = Join-Path $Root 'venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = 'python'
}

Write-Host "Stopping anything on port $Port..."
Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object {
        try { Stop-Process -Id $_ -Force -ErrorAction Stop } catch {}
    }
Start-Sleep -Seconds 1

Write-Host 'Verifying critical routes...'
& $python manage.py shell -c @"
from django.urls import reverse
from planning.models import Plan
assert 'published_at' in {f.name for f in Plan._meta.get_fields()}, 'Plan.published_at missing — migrate?'
print('OK', reverse('production-stations'), reverse('planning-portal-today'))
"@
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Starting runserver on :$Port (Ctrl+C to stop)"
& $python manage.py runserver $Port
