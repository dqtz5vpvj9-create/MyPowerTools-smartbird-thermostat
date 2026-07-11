#! /usr/bin/env pwsh
[CmdletBinding()]
param (
    [Switch]$SendPid
)

$androidtools_dir = [AospSettingsMgr]::new().GetAndroidToolsDir()
. (Resolve-Path (Join-Path $androidtools_dir -ChildPath "pwsh_modules\lib_aosp_testing.ps1"))
cd /home/lixr/repo/ndkrepo
as "date $(Get-Date -UFormat +%m%d%H%M%Y.%S)" | Out-Null
as "mkdir -p /dev/memcg/test_art/"
as "mount -t debugfs none /sys/kernel/debug/"
while($true) {
as "echo 20M > /dev/memcg/test_art/memory.limit_in_bytes"
Write-Output "Waiting for art.pid"
$art_pid = GetLastRunningDalvikVM -Waiting
if($art_pid -ne -1) {
    Write-Output "Found pid: $art_pid"
    as "echo $art_pid > /dev/memcg/test_art/tasks"
    as cat /dev/memcg/test_art/tasks
}

}

# Read-Host "Press any key to set memory limit for $pid_out"
# if ([int]::Parse($p) -gt 0) {
#     Write-Output "prctl.out pid is $p"
#     as "echo $p > /dev/memcg/prctl/tasks"
# }
cd -
