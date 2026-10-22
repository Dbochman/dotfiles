# Read-only attended Windows inventory. No credentials, process arguments,
# profiles, update installation, service changes, or recursive user-file scan.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$observed = [DateTime]::UtcNow.ToString('o')
$os = Get-CimInstance Win32_OperatingSystem
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$nodes = @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe'")
$binaries = @($nodes | Where-Object { $_.ExecutablePath } |
    Select-Object -ExpandProperty ExecutablePath -Unique | ForEach-Object {
        $version = (Get-Item -LiteralPath $_).VersionInfo.FileVersion
        [pscustomobject]@{
            path = $_; resolved = $_; executable = $true
            version = @{ status = 'observed'; text = $version }
            evidence = 'running_path_file_version_not_memory_probe'
        }
    })
$processes = @($nodes | ForEach-Object {
    [pscustomobject]@{ pid = $_.ProcessId; executable = $_.ExecutablePath
        mapped_executables = @($_.ExecutablePath | Where-Object { $_ })
        evidence = 'process_executable_path_no_arguments' }
})
$defender = @{ status = 'unavailable' }
try {
    $mp = Get-MpComputerStatus
    $defender = @{ status = 'observed'; platform = $mp.AMProductVersion
        engine = $mp.AMEngineVersion; signature = $mp.AntivirusSignatureVersion
        signature_updated = $mp.AntivirusSignatureLastUpdated.ToUniversalTime().ToString('o')
        antivirus_enabled = $mp.AntivirusEnabled; real_time = $mp.RealTimeProtectionEnabled }
} catch { }
$appx = @(Get-AppxPackage | Where-Object { $_.Name -match 'OpenAI|Codex|Claude' } |
    ForEach-Object { @{ name = $_.Name; version = $_.Version.ToString(); evidence = 'appx_package_metadata' } })
$hotfixes = @(Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10 |
    ForEach-Object { @{ id = $_.HotFixID; installed_on = $_.InstalledOn.ToString('yyyy-MM-dd') } })
$browsers = @()
foreach ($entry in @(
    @{ name = 'Google Chrome'; path = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" },
    @{ name = 'Microsoft Edge'; path = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe" }
)) {
    if (Test-Path -LiteralPath $entry.path) {
        $browsers += @{ name = $entry.name; version = (Get-Item -LiteralPath $entry.path).VersionInfo.ProductVersion
                       evidence = 'installed_file_metadata_not_launch_probe' }
    }
}
[pscustomobject]@{
    schema_version = 1; observed_at = $observed; os = 'Windows'; coverage = 'partial'
    vulnerability_clearance = $false; architecture = $os.OSArchitecture
    os_version = @{ status = 'observed'; text = "10.0.$($cv.CurrentBuildNumber).$($cv.UBR)" }
    os_release = @{ PRETTY_NAME = $os.Caption; VERSION_ID = "10.0.$($cv.CurrentBuildNumber).$($cv.UBR)" }
    processes = @{ status = 'observed'; processes = $processes; coverage = 'node_executable_paths_only' }
    node_binaries = $binaries
    windows = @{ display_version = $cv.DisplayVersion; defender = $defender; apps = $appx
        browsers = $browsers; hotfixes = $hotfixes
        reboot_pending = (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') }
    limitations = @('Installed versions do not prove vendor backports or exploitability',
        'No process arguments, environment, credentials, account state or user documents read',
        'Only active Node paths, selected app packages and two browser locations checked',
        'No Windows Update scan or installation performed; hotfix list is not complete patch proof')
} | ConvertTo-Json -Depth 8 -Compress
