param(
    [Parameter(Mandatory = $true)]
    [string]$Runner,

    [Parameter(Mandatory = $true)]
    [string]$Node,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $OutputRoot) {
    throw 'Test output directory must be new'
}
[void][System.IO.Directory]::CreateDirectory($OutputRoot)
$program = Join-Path $OutputRoot 'argv-check.cjs'
$actualPath = Join-Path $OutputRoot 'arguments.json'
$receipt = Join-Path $OutputRoot 'native-receipt.json'
[System.IO.File]::WriteAllText(
    $program,
    'require("fs").writeFileSync(process.argv[2], JSON.stringify(process.argv.slice(3)));'
)
[string[]]$expected = @('two words', '', 'quote"inside', 'trail\', '-g', '0')
[string[]]$arguments = @($program, $actualPath) + $expected
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(
    (ConvertTo-Json -InputObject $arguments -Compress)
))
foreach ($attempt in @(1, 2)) {
    & "$PSHOME\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass `
        -File $Runner -Executable $Node -ReceiptFile $receipt -ArgumentsBase64 $encoded
    if ($LASTEXITCODE -ne 0) {
        throw "Native wrapper failed on attempt $attempt"
    }
    [string[]]$actual = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($actualPath))
    if ($actual.Count -ne $expected.Count) {
        throw 'Native argument count changed'
    }
    for ($index = 0; $index -lt $expected.Count; $index++) {
        if ($actual[$index] -cne $expected[$index]) {
            throw "Native argument changed at index $index"
        }
    }
    $state = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
    if ($state.state -ne 'complete' -or $state.treeCleanupVerified -ne $true -or
        $state.exitCode -ne 0 -or $state.rootPid -le 0 -or $state.targetPid -le 0) {
        throw 'Durable completion receipt is invalid'
    }
}
[string[]]$failureArguments = @('-e', 'process.exit(7)')
$failureEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(
    (ConvertTo-Json -InputObject $failureArguments -Compress)
))
& "$PSHOME\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass `
    -File $Runner -Executable $Node -ReceiptFile $receipt -ArgumentsBase64 $failureEncoded
if ($LASTEXITCODE -ne 7) {
    throw 'Native nonzero exit code was not preserved'
}
$failureState = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
if ($failureState.state -ne 'complete' -or $failureState.treeCleanupVerified -ne $true -or
    $failureState.exitCode -ne 7) {
    throw 'Clean nonzero completion receipt is invalid'
}
[ordered]@{
    ok = $true
    powershellVersion = $PSVersionTable.PSVersion.ToString()
    argumentRoundTrips = 2
    receiptReplacementVerified = $true
    nonzeroExitPreserved = $true
} | ConvertTo-Json -Compress
exit 0
