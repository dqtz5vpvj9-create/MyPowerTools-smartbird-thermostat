#! /usr/bin/env pwsh
[CmdletBinding()]
param (
    [Switch]$SkipCompile
)

as "date $(Get-Date -UFormat +%m%d%H%M%Y.%S)" | Out-Null
$memcg_root = "/dev/memcg"
$memcg_name = "cpfd_stress"

$pid_file = "/data/local/tmp/cpfd_stress.pid"

as mkdir -p "$memcg_root/$memcg_name"
as "echo 10M > $memcg_root/$memcg_name/memory.limit_in_bytes"

# The comp_stress_test binary will set the memory cgroup for itself, so this script can just exit.
return
Write-Output "Waiting for cpfd_stress.pid"
$found_pid = $false
as rm $pid_file
as touch $pid_file
# as sync

$max_retries = 0
$retry_count = 0

$start_date = Get-Date
while ($false) {
    $ls_out = (as ls -ll $pid_file *>&1)
    Write-Debug $ls_out
    if([String]::IsNullOrWhiteSpace($ls_out)) {
        continue
    }
    if ($ls_out | Select-String "No such file or directory") {
        continue
    }
    if ($ls_out | Select-String "device '$Serial' not found") {
        exit
    }
    # Get the date from the ls output
    $pattern = "(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}).*cpfd_stress\.pid"
    # Get YYYY-MM-DD HH:MM from the ls output
    if ($ls_out -match $pattern) {
        $date = $matches[1]
        # Convert the date to a datetime object
        $date = [datetime]$date
        # if date is later than start date, then we have found the pid
        if ($date -gt $start_date) {
            $found_pid = $true
            # cat the pid file
            $pid_out = (as cat $pid_file)
            # check if the pid is valid
            if ($pid_out -match "\d+") {
                # $pid_out = $matches[0]
                Write-Output "Found pid: $pid_out"
            }
            else {
                Write-Error "Invalid pid: $pid_out"
                $retry_count++
                if ($retry_count -lt $max_retries) {
                    Write-Output "Retrying in 1 second..."
                    Start-Sleep -Seconds 1
                }
                $found_pid = $false
            }
        }
        else {
            Write-Debug "$date is not later than $start_date"
        }
    }
    else {
        Write-Output "ls_out = $ls_out"
    }
}

as "echo $pid_out > $memcg_root/$memcg_name/tasks"
as cat "$memcg_root/$memcg_name/tasks"
