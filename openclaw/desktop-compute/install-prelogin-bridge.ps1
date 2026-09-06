[CmdletBinding()]
param(
    [string]$Distribution = "Ubuntu",
    [string]$TaskName = "OpenClaw WSL SSH Bridge"
)

$ErrorActionPreference = "Stop"
$wsl = Join-Path $env:WINDIR "System32\wsl.exe"
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path -LiteralPath $wsl -PathType Leaf)) {
    throw "wsl.exe is unavailable"
}

$distributions = & $wsl --list --quiet 2>$null
if ($LASTEXITCODE -ne 0 -or $distributions -notcontains $Distribution) {
    throw "The requested WSL distribution is not registered for $identity"
}

& $wsl -d $Distribution -u root -- test -x /usr/local/sbin/openclaw-wsl-reverse-ssh
if ($LASTEXITCODE -ne 0) {
    throw "The root-owned WSL bridge helper is not installed"
}

$arguments = "-d $Distribution -u root -- /usr/local/sbin/openclaw-wsl-reverse-ssh"
$action = New-ScheduledTaskAction -Execute $wsl -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT30S"
$principal = New-ScheduledTaskPrincipal `
    -UserId $identity `
    -LogonType S4U `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 12 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

$task = New-ScheduledTask `
    -Action $action `
    -Description "Starts Ubuntu WSL, sshd, and the private reverse SSH bridge before interactive Windows login." `
    -Principal $principal `
    -Settings $settings `
    -Trigger $trigger

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3

$registered = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
[pscustomobject]@{
    TaskName = $registered.TaskName
    State = [string]$registered.State
    Principal = $registered.Principal.UserId
    LogonType = [string]$registered.Principal.LogonType
    LastTaskResult = $info.LastTaskResult
    NextStep = "Keep the user Startup fallback until one cold-boot pre-login canary succeeds."
} | ConvertTo-Json -Compress
