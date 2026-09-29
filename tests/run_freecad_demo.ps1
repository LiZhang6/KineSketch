$ErrorActionPreference = 'Stop'

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$prefix = $env:CONDA_PREFIX
if (-not $prefix) { throw 'Run this script through pixi run demo-native.' }
$manifest = $env:KINESKETCH_PARTS_MANIFEST
if ($manifest) {
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
        throw "KINESKETCH_PARTS_MANIFEST does not name a file: $manifest"
    }
    $manifest = (Resolve-Path -LiteralPath $manifest).Path
}
$freecad = Join-Path $prefix 'Library\bin\freecad.exe'
$macro = Join-Path $PSScriptRoot 'run_freecad_gui.FCMacro'
if (-not (Test-Path -LiteralPath $freecad -PathType Leaf)) { throw "Missing FreeCAD GUI: $freecad" }
$env:PATH = $prefix + ';' + (Join-Path $prefix 'Library\bin') + ';' + (Join-Path $prefix 'Scripts') + ';' + $env:PATH

$runId = [guid]::NewGuid().ToString('N')
$runDirectory = Join-Path $root ('.pixi\native-test-runs\' + $runId)
$demoDirectory = Join-Path $root ('outputs\demo_native_' + $runId)
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
$tempDirectory = Join-Path $runDirectory 'temp'
New-Item -ItemType Directory -Path $tempDirectory -Force | Out-Null
$env:TEMP = $tempDirectory
$env:TMP = $tempDirectory
$report = Join-Path $runDirectory 'result.json'
$requestPath = Join-Path $runDirectory 'demo_request.json'
$request = @{ output_dir = $demoDirectory }
if ($manifest) { $request.manifest_path = $manifest }
$request | ConvertTo-Json | Set-Content -LiteralPath $requestPath -Encoding UTF8

$env:KINESKETCH_PROJECT_ROOT = $root
$env:KINESKETCH_TEST_REPORT = $report
$env:FREECAD_USER_HOME = Join-Path $root '.pixi\freecad-user-tests'
$env:KINESKETCH_PARTS_MANIFEST = $null
New-Item -ItemType Directory -Path $env:FREECAD_USER_HOME -Force | Out-Null

for ($attempt = 1; $attempt -le 3; $attempt++) {
    $stdout = Join-Path $runDirectory "stdout.$attempt.log"
    $stderr = Join-Path $runDirectory "stderr.$attempt.log"
    $process = Start-Process -FilePath $freecad -ArgumentList @($macro) -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    if (-not $process.WaitForExit(180000)) {
        if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
        throw "FreeCAD demo timed out; see $runDirectory"
    }
    if (Test-Path -LiteralPath $report -PathType Leaf) { break }
    Write-Output "FreeCAD launch $attempt ended before writing a demo report; logs: $stdout $stderr"
    if ($attempt -lt 3) { Start-Sleep -Seconds 10 }
    if (Test-Path -LiteralPath $report -PathType Leaf) { break }
    if (Test-Path -LiteralPath $demoDirectory) { break }
}
if (-not (Test-Path -LiteralPath $report -PathType Leaf)) {
    throw "FreeCAD did not write a demo report; see $runDirectory"
}
$result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
if ($result.status -ne 'capture_success') {
    Write-Output $result.exception
    Get-Content -LiteralPath $stderr -Tail 80
    throw "FreeCAD demo failed; see $report"
}
$python = Join-Path $prefix 'python.exe'
$captureReport = $result.capture.artifacts.capture_report
$videoJson = & $python -m freecad.KineSketch.skills.kinematic.scripts.encode_video $captureReport
if ($LASTEXITCODE -ne 0) { throw "Video encoding failed: $videoJson" }
$video = $videoJson | ConvertFrom-Json
if ($video.status -ne 'success') { throw "Video encoding failed: $videoJson" }
$result | Add-Member -NotePropertyName video -NotePropertyValue $video
$result.status = 'success'
$updated = $result | ConvertTo-Json -Depth 32
Set-Content -LiteralPath $report -Value $updated -Encoding UTF8
Set-Content -LiteralPath (Join-Path $demoDirectory 'demo_result.json') -Value $updated -Encoding UTF8
Write-Output "FreeCAD $($result.freecad_version -join '.') demo=success"
Write-Output "Result: $demoDirectory"
Write-Output "Assembly: $($result.assembly.artifacts.assembly)"
Write-Output "CSV: $($result.kinematic.artifacts.'motion.csv')"
Write-Output "Video: $($result.video.artifacts.video)"
Write-Output "Report: $report"
