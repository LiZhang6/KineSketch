[CmdletBinding(SupportsShouldProcess)]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$artifactNames = @(
    "build",
    "dist",
    "sdist",
    "wheels",
    ".eggs",
    ".pytest_cache",
    ".ruff_cache"
)

foreach ($name in $artifactNames) {
    $path = Join-Path $projectRoot $name
    if ((Test-Path -LiteralPath $path) -and $PSCmdlet.ShouldProcess($path, "Remove build artifact")) {
        Remove-Item -LiteralPath $path -Recurse -Force
        Write-Host "Removed $path"
    }
}

$sourceRoots = @(
    (Join-Path $projectRoot "freecad"),
    (Join-Path $projectRoot "scripts")
)

foreach ($sourceRoot in $sourceRoots) {
    if (-not (Test-Path -LiteralPath $sourceRoot)) {
        continue
    }

    $cacheDirectories = @(
        Get-ChildItem -LiteralPath $sourceRoot -Directory -Recurse -Force |
            Where-Object { $_.Name -eq "__pycache__" -or $_.Name -like "*.egg-info" }
    )
    foreach ($directory in $cacheDirectories) {
        if ($PSCmdlet.ShouldProcess($directory.FullName, "Remove generated directory")) {
            Remove-Item -LiteralPath $directory.FullName -Recurse -Force
            Write-Host "Removed $($directory.FullName)"
        }
    }

    $bytecodeFiles = @(
        Get-ChildItem -LiteralPath $sourceRoot -File -Recurse -Force |
            Where-Object { $_.Extension -in ".pyc", ".pyo" }
    )
    foreach ($file in $bytecodeFiles) {
        if ($PSCmdlet.ShouldProcess($file.FullName, "Remove Python bytecode")) {
            Remove-Item -LiteralPath $file.FullName -Force
            Write-Host "Removed $($file.FullName)"
        }
    }
}

Write-Host "Build artifacts cleaned."