# setup_postgres.ps1
# ─────────────────────────────────────────────────────────────────────────────
# Sets up PostgreSQL for DevTwin without Docker.
# Run this script from the backend/ directory in an elevated PowerShell prompt.
#
# What it does:
#   1. Checks if PostgreSQL is already installed and running
#   2. Installs PostgreSQL 16 via winget if not found
#   3. Creates the 'devtwin' database and user
#   4. Copies .env.example → .env with the correct DATABASE_URL
# ─────────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"

Write-Host "═══════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  DevTwin PostgreSQL Setup             " -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════" -ForegroundColor Cyan

# ── 1. Check if psql is already on PATH ──────────────────────────────────────
$psqlPath = Get-Command psql -ErrorAction SilentlyContinue

if ($null -eq $psqlPath) {
    Write-Host "`n[1/3] PostgreSQL not found. Installing via winget..." -ForegroundColor Yellow
    Write-Host "      (This may take a few minutes)" -ForegroundColor Gray

    # PostgreSQL 16 from the official package
    winget install --id PostgreSQL.PostgreSQL.16 --accept-package-agreements --accept-source-agreements

    # Refresh PATH so psql is found in this session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")

    $psqlPath = Get-Command psql -ErrorAction SilentlyContinue
    if ($null -eq $psqlPath) {
        Write-Host "`n⚠  Could not find psql after install." -ForegroundColor Red
        Write-Host "   Try opening a NEW PowerShell window and re-running this script," -ForegroundColor Yellow
        Write-Host "   or manually add PostgreSQL's bin folder to your PATH." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "   ✓ PostgreSQL installed." -ForegroundColor Green
} else {
    Write-Host "`n[1/3] PostgreSQL already installed at: $($psqlPath.Source)" -ForegroundColor Green
}

# ── 2. Create database and user ───────────────────────────────────────────────
Write-Host "`n[2/3] Creating database and user..." -ForegroundColor Yellow
Write-Host "      (You may be prompted for the PostgreSQL superuser password)" -ForegroundColor Gray

$sql = @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'devtwin') THEN
    CREATE ROLE devtwin LOGIN PASSWORD 'devtwin_pass';
    RAISE NOTICE 'Created role devtwin';
  ELSE
    RAISE NOTICE 'Role devtwin already exists';
  END IF;
END
`$`$;

SELECT 'CREATE DATABASE devtwin'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'devtwin')\gexec

GRANT ALL PRIVILEGES ON DATABASE devtwin TO devtwin;
"@

$sql | psql -U postgres

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n⚠  psql command failed. Make sure the PostgreSQL service is running:" -ForegroundColor Red
    Write-Host "   Start-Service postgresql-x64-16" -ForegroundColor Yellow
    exit 1
}
Write-Host "   ✓ Database 'devtwin' and user 'devtwin' ready." -ForegroundColor Green

# ── 3. Create .env if it doesn't exist ────────────────────────────────────────
Write-Host "`n[3/3] Setting up .env file..." -ForegroundColor Yellow

if (Test-Path ".env") {
    Write-Host "   .env already exists — skipping. Edit it manually if needed." -ForegroundColor Gray
} else {
    Copy-Item ".env.example" ".env"
    Write-Host "   ✓ Copied .env.example → .env" -ForegroundColor Green
    Write-Host "   ⚠  Open .env and add your GITHUB_TOKEN!" -ForegroundColor Yellow
}

Write-Host "`n═══════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Setup complete!                       " -ForegroundColor Green
Write-Host "═══════════════════════════════════════" -ForegroundColor Cyan
Write-Host @"

Next steps:
  1. Edit .env and add your GITHUB_TOKEN
  2. Activate the virtual environment:
       .venv\Scripts\Activate.ps1
  3. Run Alembic migration:
       alembic revision --autogenerate -m "initial schema"
       alembic upgrade head
  4. Start the server:
       uvicorn app.main:app --reload

"@ -ForegroundColor White
