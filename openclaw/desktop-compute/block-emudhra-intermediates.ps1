<#
Incident-specific explicit distrust for the current Windows user.
Input: https://emudhra.we-have-a-problem.com/impacted-intermediates.json
Pins: https://bugzilla.mozilla.org/show_bug.cgi?id=2083449 (description/comment 2).
Default is read-only. -Apply requires a new receipt path. Never adds trust,
removes roots, edits signed apps, or changes execution policy. This does not
cover independent Node/Python/Java stores or other Windows user accounts.
#>
[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][string]$InventoryPath,
 [switch]$Apply,
 [string]$ReceiptPath
)
$ErrorActionPreference='Stop'
$pins=@(
 '42C4ACCD12C4F59597925E1A08A7A90102608543809308D50EC427E118BA1493',
 '45A883F349AEDFD5F3A649B5498FFB121712BA8B448F3F2A91DBC5A766FCDDBE',
 '98B896625C0C230ADEEC3EB2CF6AAD5001ECF9156A68644E82374CE5833BC25B',
 '5F1DED4DD590AF74631290CF073B122A2AAC6A1FA479778A0BD43579BD832699',
 'E7568FE6E12331F4FD7DE61DA4C7559D0F8EB192A218D4741A3BA75184333838',
 '579480AEA166290BD3C643589E795FC0D0E54E86505D180F1DFF99EFD3199512',
 '0164D7CEAE5BB909C272AF151FF44B1177AE9B47D9424B94F1D9558C30934D19',
 '62B7F9868263F72F6911FC78CEAF72C1979158B7368BF648683BB2AA39B02E72',
 'CB6E15165AC802E9B3F993F16C25D41F4E81C21EBDE71AE1B012AC00BF9C7C3F',
 '7485B51C0ED192878FA1D2F948FB529AE1842CC5086C4B67F7BE5B2039DE9489',
 '432EC25F0D28DE9006D97BF752F17BBB2CD41F669933FCB072D9A1148D0261F3',
 'FD9D5C2DBBD4995AB69B8D50C439D9527B00529199C2FD01B2174EB58F07021F',
 '9E39DE2881273ACC225A149C72BA3E0F51424963669AB633EE1DC7DF5DB067B4',
 'A496E23AC0CAB8155FC9B117B97EAE368EFCBB9041AEC8804D5A308D2D01C2D8',
 '4B2460A466F95FD40113BCDCCA2CEB29B3B093921DE45474DA7DF48874C630B1',
 '84112972B8E59D14560AE7E7119654474AAD119990CF3722BFC1E3B91615E1FD'
)
$rows=Get-Content -LiteralPath $InventoryPath -Raw | ConvertFrom-Json
if ($rows.Count -ne $pins.Count) { throw 'Expected exactly 16 published intermediates' }
$certs=@()
$seen=@{}
$sha=[Security.Cryptography.SHA256]::Create()
try {
 foreach ($row in $rows) {
  $der=[Convert]::FromBase64String(($row.pem -replace '-----BEGIN CERTIFICATE-----|-----END CERTIFICATE-----|\s',''))
  $hash=([BitConverter]::ToString($sha.ComputeHash($der))).Replace('-','')
  if ($pins -notcontains $hash -or $hash -cne $row.sha256 -or $seen.ContainsKey($hash)) { throw 'Certificate fingerprint mismatch or duplicate' }
  $seen[$hash]=$true
  $cert=New-Object Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList @(,$der)
  $certs += $cert
 }
} finally { $sha.Dispose() }
if ($Apply) {
 if (!$ReceiptPath -or ![IO.Path]::IsPathRooted($ReceiptPath) -or (Test-Path -LiteralPath $ReceiptPath)) { throw 'Apply requires a new absolute receipt path' }
 $parent=Split-Path -Parent $ReceiptPath
 if (!(Test-Path -LiteralPath $parent -PathType Container)) { throw 'Receipt parent must already exist' }
}
$store=New-Object Security.Cryptography.X509Certificates.X509Store -ArgumentList 'Disallowed','CurrentUser'
$actions=New-Object 'System.Collections.Generic.List[object]'
$receiptStream=$null
try {
 # Reserve the receipt before making any trust changes; never overwrite one.
 if ($Apply) { $receiptStream=[IO.File]::Open($ReceiptPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read) }
 $mode=if ($Apply) { [Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite } else { [Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly }
 $store.Open($mode)
 foreach ($cert in $certs) {
  $present=$store.Certificates.Find([Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,$cert.Thumbprint,$false).Count -gt 0
  $action=if ($present) {'already_present'} elseif ($Apply) {'added'} else {'would_add'}
  if (!$present -and $Apply) { $store.Add($cert) }
  $record=[pscustomobject]@{subject=$cert.Subject;thumbprint=$cert.Thumbprint;action=$action}
  $actions.Add($record)
  $record | ConvertTo-Json -Compress
 }
 if ($Apply) {
  foreach ($cert in $certs) {
   if ($store.Certificates.Find([Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,$cert.Thumbprint,$false).Count -eq 0) {throw 'Distrust store read-back failed'}
  }
 }
} finally {
 $store.Close()
 if ($null -ne $receiptStream) {
  try {
   $receipt=[pscustomobject]@{utc=[DateTime]::UtcNow.ToString('o');store='CurrentUser/Disallowed';actions=@($actions.ToArray())}
   $bytes=[Text.Encoding]::UTF8.GetBytes(($receipt | ConvertTo-Json -Depth 5))
   $receiptStream.Write($bytes,0,$bytes.Length)
  } finally { $receiptStream.Dispose() }
 }
 foreach ($cert in $certs) { $cert.Dispose() }
}
