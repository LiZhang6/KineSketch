[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$prefix = Join-Path $root '.pixi\envs\default'
$freecadExe = Join-Path $prefix 'Library\bin\freecad.exe'
if (-not (Test-Path -LiteralPath $freecadExe -PathType Leaf)) {
    throw "FreeCAD is not installed in the project environment: $freecadExe"
}

$env:CONDA_PREFIX = $prefix
$env:PIXI_PROJECT_ROOT = $root
$env:PYTHONPATH = (Join-Path $prefix 'Library\bin') + ';' + (Join-Path $prefix 'Library\mod\Assembly') + ';' + $root
$env:PATH = $prefix + ';' + (Join-Path $prefix 'Library\bin') + ';' + (Join-Path $prefix 'Scripts') + ';' + $env:PATH
$openssh = 'C:\Program Files\OpenSSH'
if (Test-Path -LiteralPath (Join-Path $openssh 'ssh.exe') -PathType Leaf) {
    $env:PATH = $openssh + ';' + $env:PATH
}
$env:FREECAD_USER_HOME = Join-Path $root '.pixi\freecad-user'
$tempDirectory = Join-Path $root '.pixi\temp'
New-Item -ItemType Directory -Path $tempDirectory -Force | Out-Null
$env:TEMP = $tempDirectory
$env:TMP = $tempDirectory

$process = Start-Process -FilePath $freecadExe -PassThru -WindowStyle Normal
Write-Output "FreeCAD GUI launched: PID=$($process.Id)"
