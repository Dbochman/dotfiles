param(
    [Parameter(Mandatory = $true)]
    [string]$PhaseScript,

    [Parameter(Mandatory = $true)]
    [string]$PidFile
)

$ErrorActionPreference = 'Stop'
$powershell = Join-Path $PSHOME 'powershell.exe'
$runner = Join-Path $PSScriptRoot 'windows_process_runner.ps1'
foreach ($dependency in @($PhaseScript, $powershell, $runner)) {
    if (-not (Test-Path -LiteralPath $dependency -PathType Leaf)) {
        throw 'Windows phase lifecycle dependency is unavailable'
    }
}

$phaseArguments = @(
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    $PhaseScript
)
$argumentsJson = ConvertTo-Json -InputObject $phaseArguments -Compress
$argumentsBase64 = [System.Convert]::ToBase64String(
    [System.Text.Encoding]::UTF8.GetBytes($argumentsJson)
)

& $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass `
    -File $runner -Executable $powershell -ReceiptFile $PidFile `
    -ArgumentsBase64 $argumentsBase64
exit $LASTEXITCODE
