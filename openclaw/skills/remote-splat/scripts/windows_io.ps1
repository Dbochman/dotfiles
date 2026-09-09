param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('copy-file', 'hash-file')]
    [string]$Operation,

    [Parameter(Mandatory = $true)]
    [string]$Source,

    [string]$Destination
)

$ErrorActionPreference = 'Stop'
$sourceItem = Get-Item -LiteralPath $Source
if (-not $sourceItem.PSIsContainer -and $sourceItem.Length -gt 0) {
    $sourceHash = (Get-FileHash -LiteralPath $sourceItem.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
} else {
    throw 'source must be a non-empty regular file'
}

if ($Operation -eq 'hash-file') {
    [ordered]@{
        ok = $true
        operation = $Operation
        sizeBytes = $sourceItem.Length
        sha256 = $sourceHash
    } | ConvertTo-Json -Compress
    exit 0
}

if ([string]::IsNullOrWhiteSpace($Destination)) {
    throw 'copy-file requires a destination'
}
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
$destinationDirectory = [System.IO.Path]::GetDirectoryName($destinationPath)
if ([string]::IsNullOrWhiteSpace($destinationDirectory)) {
    throw 'destination directory is invalid'
}
[System.IO.Directory]::CreateDirectory($destinationDirectory) | Out-Null

if ([System.IO.File]::Exists($destinationPath)) {
    $destinationItem = Get-Item -LiteralPath $destinationPath
    $destinationHash = (Get-FileHash -LiteralPath $destinationPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($destinationItem.Length -ne $sourceItem.Length -or $destinationHash -ne $sourceHash) {
        throw 'destination already contains a different file'
    }
    [ordered]@{
        ok = $true
        operation = $Operation
        alreadyPresent = $true
        sizeBytes = $sourceItem.Length
        sha256 = $sourceHash
    } | ConvertTo-Json -Compress
    exit 0
}

$temporary = Join-Path $destinationDirectory ('.openclaw-copy-{0}-{1}.tmp' -f $PID, [guid]::NewGuid().ToString('N'))
try {
    [System.IO.File]::Copy($sourceItem.FullName, $temporary, $false)
    $temporaryItem = Get-Item -LiteralPath $temporary
    $temporaryHash = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($temporaryItem.Length -ne $sourceItem.Length -or $temporaryHash -ne $sourceHash) {
        throw 'native copy verification failed'
    }
    [System.IO.File]::Move($temporary, $destinationPath)
} finally {
    if ([System.IO.File]::Exists($temporary)) {
        Remove-Item -LiteralPath $temporary -Force
    }
}

[ordered]@{
    ok = $true
    operation = $Operation
    alreadyPresent = $false
    sizeBytes = $sourceItem.Length
    sha256 = $sourceHash
} | ConvertTo-Json -Compress
