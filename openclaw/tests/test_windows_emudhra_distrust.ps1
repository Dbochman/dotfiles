# Run on Windows against the verified public inventory. No Pester dependency.
# All mutation tests use malformed inputs that must fail before opening a store.
[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][string]$ScriptPath,
 [Parameter(Mandatory=$true)][string]$InventoryPath,
 [Parameter(Mandatory=$true)][string]$TempDirectory
)
$ErrorActionPreference='Stop'
if (!(Test-Path -LiteralPath $TempDirectory -PathType Container)) {throw 'An existing audit temp directory is required'}
$rows=Get-Content -LiteralPath $InventoryPath -Raw | ConvertFrom-Json
function Assert-Rejected([string]$name,[string]$message,[scriptblock]$action) {
 try { & $action | Out-Null } catch {
  if ($_.Exception.Message -notmatch $message) {throw "Unexpected failure in ${name}: $($_.Exception.Message)"}
  Write-Output "PASS $name"
  return
 }
 throw "Unsafe input accepted: $name"
}
$good=@(& $ScriptPath -InventoryPath $InventoryPath)
if ($good.Count -ne 16) {throw 'Valid inventory did not produce 16 results'}
foreach ($line in $good) { if (($line | ConvertFrom-Json).action -notin @('would_add','already_present')) {throw 'Dry run unexpectedly mutated trust'} }
Write-Output 'PASS valid inventory read-only'
$bad=Join-Path $TempDirectory ('bad-inventory-'+[Guid]::NewGuid().ToString('N')+'.json')
$rows[0..14] | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $bad -Encoding UTF8
Assert-Rejected 'incomplete inventory' 'exactly 16' { & $ScriptPath -InventoryPath $bad -Apply }
$rows[0].sha256='0'*64
$rows | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $bad -Encoding UTF8
Assert-Rejected 'fingerprint mismatch' 'fingerprint mismatch' { & $ScriptPath -InventoryPath $bad -Apply }
$rows=Get-Content -LiteralPath $InventoryPath -Raw | ConvertFrom-Json
$rows[0]=$rows[1]
$rows | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $bad -Encoding UTF8
Assert-Rejected 'duplicate certificate' 'duplicate' { & $ScriptPath -InventoryPath $bad -Apply }
Assert-Rejected 'missing receipt' 'new absolute receipt path' { & $ScriptPath -InventoryPath $InventoryPath -Apply }
Assert-Rejected 'existing receipt protected' 'new absolute receipt path' { & $ScriptPath -InventoryPath $InventoryPath -Apply -ReceiptPath $bad }
Assert-Rejected 'relative receipt refused' 'new absolute receipt path' { & $ScriptPath -InventoryPath $InventoryPath -Apply -ReceiptPath 'receipt.json' }
Write-Output 'All seven distrust-script checks passed; no trust changes made by tests.'
