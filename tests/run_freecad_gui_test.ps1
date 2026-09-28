$ErrorActionPreference = 'Stop'

$workspaceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$prefix = $env:CONDA_PREFIX
if (-not $prefix) { throw 'Run this script through pixi run test-native.' }
$manifest = $env:KINESKETCH_PARTS_MANIFEST
if ($manifest) {
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
        throw "KINESKETCH_PARTS_MANIFEST does not name a file: $manifest"
    }
    $manifest = (Resolve-Path -LiteralPath $manifest).Path
}
$freecadExe = Join-Path $prefix 'Library\bin\freecad.exe'
$macro = Join-Path $PSScriptRoot 'run_freecad_gui.FCMacro'
if (-not (Test-Path -LiteralPath $freecadExe -PathType Leaf)) { throw "Missing FreeCAD GUI: $freecadExe" }

$runId = [guid]::NewGuid().ToString('N')
$runDirectory = Join-Path $workspaceRoot ('.pixi\native-test-runs\' + $runId)
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
if ($manifest) {
    @{ manifest_path = $manifest } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runDirectory 'parts_request.json') -Encoding UTF8
}
$report = Join-Path $runDirectory 'result.json'

$env:KINESKETCH_PROJECT_ROOT = $workspaceRoot
$env:KINESKETCH_TEST_REPORT = $report
$env:FREECAD_USER_HOME = Join-Path $workspaceRoot '.pixi\freecad-user-tests'
$env:KINESKETCH_PARTS_MANIFEST = $null
New-Item -ItemType Directory -Path $env:FREECAD_USER_HOME -Force | Out-Null

for ($attempt = 1; $attempt -le 3; $attempt++) {
    $stdout = Join-Path $runDirectory "stdout.$attempt.log"
    $stderr = Join-Path $runDirectory "stderr.$attempt.log"
    $process = Start-Process -FilePath $freecadExe -ArgumentList @($macro) -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    if (-not $process.WaitForExit(180000)) {
        if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
        throw "FreeCAD GUI test timed out; see $runDirectory"
    }
    if (Test-Path -LiteralPath $report -PathType Leaf) { break }
    Write-Output "FreeCAD launch $attempt ended before writing a test report; logs: $stdout $stderr"
    if ($attempt -lt 3) { Start-Sleep -Seconds 10 }
}
if (-not (Test-Path -LiteralPath $report -PathType Leaf)) {
    throw "FreeCAD did not write a test result; see $runDirectory"
}

$result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
Write-Output "FreeCAD $($result.freecad_version -join '.') GUI=$($result.gui_up) tests=$($result.tests_run) failures=$($result.failures.Count) errors=$($result.errors.Count) skipped=$($result.skipped.Count)"
Write-Output "Report: $report"
if (-not $result.ok) {
    Get-Content -LiteralPath $stderr | Select-Object -Last 80
    if ($result.exception) { Write-Output $result.exception }
    exit 1
}
