param(
    [Parameter(Mandatory = $true)]
    [string]$Executable,

    [Parameter(Mandatory = $true)]
    [string]$ReceiptFile,

    [Parameter(Mandatory = $true)]
    [string]$ArgumentsBase64
)

$ErrorActionPreference = 'Stop'

function Write-NativeReceipt {
    param(
        [Parameter(Mandatory = $true)]
        [string]$State,

        [Parameter(Mandatory = $true)]
        [bool]$TreeCleanupVerified,

        [AllowNull()]
        [Nullable[int]]$TargetPid,

        [AllowNull()]
        [Nullable[int]]$ExitCode
    )

    $payload = [ordered]@{
        schemaVersion = 1
        state = $State
        rootPid = $PID
        targetPid = $TargetPid
        treeCleanupVerified = $TreeCleanupVerified
        exitCode = $ExitCode
    } | ConvertTo-Json -Compress
    $temporary = '{0}.tmp.{1}' -f $receiptPath, $PID
    try {
        [System.IO.File]::WriteAllText($temporary, "${payload}`n")
        if ([System.IO.File]::Exists($receiptPath)) {
            [System.IO.File]::Replace($temporary, $receiptPath, [NullString]::Value)
        } else {
            [System.IO.File]::Move($temporary, $receiptPath)
        }
    } finally {
        if ([System.IO.File]::Exists($temporary)) {
            [System.IO.File]::Delete($temporary)
        }
    }
}

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }
    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append([char]34)
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $backslashes += 1
        } elseif ($character -eq [char]34) {
            [void]$builder.Append([char]92, 2 * $backslashes + 1)
            [void]$builder.Append([char]34)
            $backslashes = 0
        } else {
            if ($backslashes -gt 0) {
                [void]$builder.Append([char]92, $backslashes)
                $backslashes = 0
            }
            [void]$builder.Append($character)
        }
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append([char]92, 2 * $backslashes)
    }
    [void]$builder.Append([char]34)
    return $builder.ToString()
}

function Get-ProcessSnapshot {
    return @(
        Get-CimInstance -ClassName Win32_Process `
            -Property ProcessId, ParentProcessId, CreationDate -ErrorAction Stop
    )
}

function Update-TrackedDescendants {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Tracked,

        [Parameter(Mandatory = $true)]
        [object[]]$Snapshot
    )

    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($candidate in $Snapshot) {
            $pidKey = [string]$candidate.ProcessId
            $parentKey = [string]$candidate.ParentProcessId
            if (-not $Tracked.ContainsKey($pidKey) -and $Tracked.ContainsKey($parentKey)) {
                $Tracked[$pidKey] = [string]$candidate.CreationDate
                $changed = $true
            }
        }
    }
}
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw 'Windows executable is unavailable'
}
try {
    $argumentsJson = [System.Text.Encoding]::UTF8.GetString(
        [System.Convert]::FromBase64String($ArgumentsBase64)
    )
    [string[]]$Arguments = ConvertFrom-Json -InputObject $argumentsJson
} catch {
    throw 'Windows executable arguments are invalid'
}

$receiptPath = [System.IO.Path]::GetFullPath($ReceiptFile)
$receiptDirectory = [System.IO.Path]::GetDirectoryName($receiptPath)
if (-not [System.IO.Directory]::Exists($receiptDirectory)) {
    throw 'Windows process receipt directory is unavailable'
}
if ([System.IO.File]::Exists($receiptPath)) {
    try {
        $existing = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    } catch {
        throw 'Existing Windows process receipt is invalid'
    }
    if (
        $existing.state -notin @('unused', 'complete') -or
        $existing.treeCleanupVerified -ne $true
    ) {
        throw 'A Windows process tree is already active or unresolved'
    }
}

$process = $null
$exitCode = 1
$completionVerified = $false
Write-NativeReceipt -State 'running' -TreeCleanupVerified $false `
    -TargetPid $null -ExitCode $null
try {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.UseShellExecute = $false
    $startInfo.Arguments = (($Arguments | ForEach-Object {
        ConvertTo-NativeArgument $_
    }) -join ' ')
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw 'Windows executable did not start'
    }
    $targetPid = $process.Id
    $tracked = @{}
    $tracked[[string]$targetPid] = '<root>'
    Write-NativeReceipt -State 'running' -TreeCleanupVerified $false `
        -TargetPid $targetPid -ExitCode $null

    while (-not $process.WaitForExit(5000)) {
        Update-TrackedDescendants -Tracked $tracked -Snapshot (Get-ProcessSnapshot)
    }
    $process.WaitForExit()
    $exitCode = $process.ExitCode

    $snapshot = Get-ProcessSnapshot
    Update-TrackedDescendants -Tracked $tracked -Snapshot $snapshot
    $liveDescendants = @(
        $snapshot | Where-Object {
            $pidKey = [string]$_.ProcessId
            $_.ProcessId -ne $targetPid -and
                $tracked.ContainsKey($pidKey) -and
                $tracked[$pidKey] -eq [string]$_.CreationDate
        }
    )
    if ($liveDescendants.Count -ne 0) {
        throw 'Windows executable left a descendant process running'
    }
    $completionVerified = $true
    Write-NativeReceipt -State 'complete' -TreeCleanupVerified $true `
        -TargetPid $targetPid -ExitCode $exitCode
} catch {
    Write-Error $_ -ErrorAction Continue
    $exitCode = 1
} finally {
    if ($null -ne $process) {
        $process.Dispose()
    }
}

if (-not $completionVerified) {
    exit 1
}
exit $exitCode
