param(
    [Parameter(Mandatory = $true)]
    [string]$PhaseScript,

    [Parameter(Mandatory = $true)]
    [string]$PidFile
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PhaseScript -PathType Leaf)) {
    throw 'Windows phase script is unavailable'
}

$pidPath = [System.IO.Path]::GetFullPath($PidFile)
$pidDirectory = [System.IO.Path]::GetDirectoryName($pidPath)
if (-not [System.IO.Directory]::Exists($pidDirectory)) {
    throw 'Windows phase PID directory is unavailable'
}
$temporary = '{0}.tmp.{1}' -f $pidPath, $PID
try {
    [System.IO.File]::WriteAllText($temporary, "${PID}`n")
    [System.IO.File]::Move($temporary, $pidPath)
} finally {
    if ([System.IO.File]::Exists($temporary)) {
        [System.IO.File]::Delete($temporary)
    }
}

try {
    & $PhaseScript
    if (-not $?) {
        exit 1
    }
    if ($null -ne $LASTEXITCODE) {
        exit $LASTEXITCODE
    }
    exit 0
} catch {
    Write-Error $_
    exit 1
}
