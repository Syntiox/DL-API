# ════════════════════════════════════════════════════
#  Syntiox DL API — Local Run Script (PowerShell)
#  Usage: .\run_local.ps1
#  Or with custom browser: .\run_local.ps1 -Browser firefox
# ════════════════════════════════════════════════════

param(
    [string]$Browser = "chrome",
    [int]$Port = 8000
)

# ── Load .env file if it exists ───────────────────────────────────────────────
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Write-Host "[STARTUP] Loading environment from .env ..." -ForegroundColor Cyan
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        # Skip comments and empty lines
        if ($line -and -not $line.StartsWith("#")) {
            $parts = $line -split "=", 2
            if ($parts.Length -eq 2) {
                $key   = $parts[0].Trim()
                $value = $parts[1].Trim()
                if ($value) {
                    [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
                    Write-Host "  SET $key" -ForegroundColor DarkGray
                }
            }
        }
    }
} else {
    Write-Host "[STARTUP] No .env file found — using defaults" -ForegroundColor Yellow
    Write-Host "  Copy .env.example → .env and fill in your values" -ForegroundColor Yellow
}

# ── Set defaults if not already set ──────────────────────────────────────────
if (-not $env:JWT_SECRET)  { $env:JWT_SECRET  = "change-me-in-production" }
if (-not $env:API_SECRET)  { $env:API_SECRET  = "change-me-in-production" }
if (-not $env:BGU_BASE_URL){ $env:BGU_BASE_URL = "http://localhost:4416" }

Write-Host ""
Write-Host "════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  Syntiox DL API — Local Dev Server" -ForegroundColor Magenta
Write-Host "════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  Port    : $Port" -ForegroundColor White
Write-Host "  Browser : $Browser (for cookie refresh)" -ForegroundColor White
Write-Host "  API Key : $($env:API_SECRET.Substring(0, [Math]::Min(6, $env:API_SECRET.Length)))..." -ForegroundColor White
Write-Host ""
Write-Host "  Swagger UI : http://localhost:$Port/docs" -ForegroundColor Green
Write-Host "  Cookie refresh (close browser first!):" -ForegroundColor Yellow
Write-Host "    POST http://localhost:$Port/cookies/refresh?browser=$Browser" -ForegroundColor Yellow
Write-Host "    Header: x-api-key: $($env:API_SECRET)" -ForegroundColor Yellow
Write-Host "════════════════════════════════════════" -ForegroundColor Magenta
Write-Host ""

# ── Start uvicorn ─────────────────────────────────────────────────────────────
uvicorn app:app --host 127.0.0.1 --port $Port --reload
