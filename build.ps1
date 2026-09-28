[CmdletBinding()]
param(
    [switch]$SkipClean,
    [switch]$NoBootstrap
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$python = (Get-Command python -ErrorAction Stop).Source

Push-Location $projectRoot
try {
    if (-not $SkipClean) {
        & (Join-Path $projectRoot "clean.ps1")
    }

    & $python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('build') else 1)"
    if ($LASTEXITCODE -ne 0) {
        if ($NoBootstrap) {
            throw "Python package 'build' is required. Install it with: python -m pip install build"
        }

        Write-Host "Installing the Python build frontend..."
        & $python -m pip install --disable-pip-version-check "build>=1.2,<2"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install the Python build frontend."
        }
    }

    Write-Host "Building KineSketch wheel and source distribution..."
    & $python -m build
    if ($LASTEXITCODE -ne 0) {
        throw "Package build failed with exit code $LASTEXITCODE."
    }

    $wheel = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot "dist") -Filter "*.whl")
    $sourceDistribution = @(
        Get-ChildItem -LiteralPath (Join-Path $projectRoot "dist") -Filter "*.tar.gz"
    )
    if ($wheel.Count -eq 0 -or $sourceDistribution.Count -eq 0) {
        throw "Build completed without both a wheel and source distribution."
    }

    Write-Host "Build completed:"
    Get-ChildItem -LiteralPath (Join-Path $projectRoot "dist") -File |
        ForEach-Object { Write-Host "  $($_.Name)" }
}
finally {
    Pop-Location
}