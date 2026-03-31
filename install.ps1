# Karakos Installer for Windows
# Run: irm https://raw.githubusercontent.com/mcarmody/karakos-package/main/install.ps1 | iex
# Or:  powershell -ExecutionPolicy Bypass -File install.ps1

param(
    [string]$InstallDir = "$env:USERPROFILE\karakos"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "Warning: $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "Error: $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "  Karakos Installer" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# --- Check Windows version ---
$osVersion = [System.Environment]::OSVersion.Version
if ($osVersion.Build -lt 19041) {
    Write-Err "Windows 10 version 2004 or later required for WSL 2."
    Write-Err "Current build: $($osVersion.Build)"
    exit 1
}

# --- Check/install winget ---
$hasWinget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $hasWinget) {
    Write-Err "winget not found. Please install App Installer from the Microsoft Store."
    Write-Err "https://apps.microsoft.com/detail/9NBLGGH4NNS1"
    exit 1
}

# --- Install Git ---
$hasGit = Get-Command git -ErrorAction SilentlyContinue
if (-not $hasGit) {
    Write-Step "Installing Git..."
    winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $hasGit = Get-Command git -ErrorAction SilentlyContinue
    if (-not $hasGit) {
        Write-Warn "Git installed but not in PATH yet. You may need to restart your terminal."
        Write-Warn "After restarting, run this script again."
        exit 1
    }
    Write-Step "Git installed."
} else {
    Write-Step "Git found: $(git --version)"
}

# --- Install Docker Desktop ---
$hasDocker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $hasDocker) {
    Write-Step "Installing Docker Desktop..."
    Write-Host "  This will install Docker Desktop with WSL 2 backend." -ForegroundColor Gray
    Write-Host "  A restart may be required after installation." -ForegroundColor Gray
    Write-Host ""

    winget install --id Docker.DockerDesktop -e --accept-source-agreements --accept-package-agreements

    Write-Warn "Docker Desktop installed."
    Write-Warn "Please:"
    Write-Warn "  1. Restart your computer if prompted"
    Write-Warn "  2. Launch Docker Desktop from the Start menu"
    Write-Warn "  3. Wait for it to finish starting (whale icon in system tray stops animating)"
    Write-Warn "  4. Run this script again"
    Write-Host ""
    Write-Step "Run this same command after Docker Desktop is running."
    exit 0
} else {
    # Check if Docker daemon is actually running
    $dockerRunning = $true
    try {
        docker info 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { $dockerRunning = $false }
    } catch {
        $dockerRunning = $false
    }

    if (-not $dockerRunning) {
        Write-Err "Docker is installed but not running."
        Write-Err "Please start Docker Desktop and wait for it to finish loading, then run this script again."
        exit 1
    }

    Write-Step "Docker found and running."
}

# --- Check Docker Compose ---
try {
    docker compose version 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "no compose" }
    Write-Step "Docker Compose available."
} catch {
    Write-Err "Docker Compose not available. Make sure Docker Desktop is up to date."
    exit 1
}

# --- Install jq ---
$hasJq = Get-Command jq -ErrorAction SilentlyContinue
if (-not $hasJq) {
    Write-Step "Installing jq..."
    winget install --id jqlang.jq -e --accept-source-agreements --accept-package-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    Write-Step "jq installed."
} else {
    Write-Step "jq found."
}

# --- Clone repository ---
if (Test-Path $InstallDir) {
    Write-Step "Directory $InstallDir already exists."
    $existing = Read-Host "  Overwrite? (y/N)"
    if ($existing -ne "y" -and $existing -ne "Y") {
        Write-Step "Keeping existing installation. Skipping clone."
    } else {
        Remove-Item -Recurse -Force $InstallDir
        Write-Step "Cloning karakos into $InstallDir..."
        git clone https://github.com/mcarmody/karakos-package.git $InstallDir
    }
} else {
    Write-Step "Cloning karakos into $InstallDir..."
    git clone https://github.com/mcarmody/karakos-package.git $InstallDir
}

Set-Location $InstallDir

# --- Run setup wizard via Git Bash ---
$gitBash = "${env:ProgramFiles}\Git\bin\bash.exe"
if (-not (Test-Path $gitBash)) {
    $gitBash = "${env:ProgramFiles(x86)}\Git\bin\bash.exe"
}
if (-not (Test-Path $gitBash)) {
    # Try PATH
    $gitBash = (Get-Command bash -ErrorAction SilentlyContinue).Source
}

if (-not $gitBash -or -not (Test-Path $gitBash)) {
    Write-Err "Cannot find bash. Please run setup manually from Git Bash:"
    Write-Err "  cd $InstallDir"
    Write-Err "  bash setup.sh"
    exit 1
}

Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "  Prerequisites installed." -ForegroundColor Cyan
Write-Host "  Launching setup wizard..." -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

& $gitBash -c "./setup.sh"

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Step "Setup complete! Starting Karakos..."
    Write-Host ""

    Set-Location "$InstallDir\config"
    docker compose up -d

    Write-Host ""
    Write-Step "Karakos is starting up. First build takes 5-10 minutes."
    Write-Host ""
    Write-Host "  Dashboard:  http://localhost:3000" -ForegroundColor Cyan
    Write-Host "  Logs:       docker compose logs -f  (run from $InstallDir\config)" -ForegroundColor Gray
    Write-Host ""
} else {
    Write-Err "Setup wizard failed or was cancelled."
    Write-Err "You can re-run it with: cd $InstallDir && bash setup.sh"
    exit 1
}
