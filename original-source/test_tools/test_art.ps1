#! /usr/bin/env pwsh
[CmdletBinding()]
param (
    [Switch]$SendPid
)
cd /home/lixr/repo/ndkrepo
as "date $(Get-Date -UFormat +%m%d%H%M%Y.%S)" | Out-Null
as "mkdir -p /dev/memcg/test_art/"
as "echo 10M > /dev/memcg/test_art/memory.limit_in_bytes"
Write-Output "Waiting for art.pid"
$found_pid = $false
$start_date = Get-Date
$iter = 0
while (!$found_pid) {
    $ls_out = (as "ls -lltr /data/local/tmp/aproc/*/art.pid" 2>&1)
    $iter += 1
    if ([String]::IsNullOrWhiteSpace($ls_out)) {
        continue
    }
    if ($ls_out | sls "device 'emulator-5554' not found") {
        exit
    }
    $ls_out | % { Write-Debug $_ }
    # Get the last line of the output
    $ls_out = ($ls_out -split "\n")[-1] 
    Write-Debug "iter $iter out: $ls_out"
    # Get the date from the ls output
    $pattern = "(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}).*art\.pid"
    $fn_pattern = "([\S]+)$"
    # Get YYYY-MM-DD HH:MM from the ls output
    if ($ls_out -match $pattern) {
        $date = $matches[1]
        # Convert the date to a datetime object
        $date = [datetime]$date
        # if date is later than start date, then we have found the pid
        if ($date -gt $start_date) {
            $found_pid = $true
            # cat the pid file
            $ls_out -match $fn_pattern
            $fn = $date = $matches[1]
            Write-Debug "Found pid file $fn"
            $pid_out = (as cat $fn)
            # check if the pid is valid
            if ($pid_out -match "\d+") {
                # $pid_out = $matches[0]
                Write-Output "Found pid: $pid_out"
                as "echo $pid_out > /dev/memcg/test_art/tasks"
                as cat /dev/memcg/test_art/tasks
                if($SendPid) {
                    $port = 11655
                    $endpoint = new-object System.Net.IPEndPoint ([system.net.ipaddress]::any, $port)
                    $listener = new-object System.Net.Sockets.TcpListener $endpoint
                    $listener.start()
                    $client = $listener.AcceptTcpClient()
                    $Stream = $client.GetStream()
                    $Response = [System.Text.Encoding]::UTF8.GetBytes("$pid_out")
                    $Stream.Write($Response, 0, $Response.Length)
                    $client.Close()
                    $listener.Stop()
                }
            }
            else {
                Write-Output "Invalid pid"
            }
        }
    }
}

# Read-Host "Press any key to set memory limit for $pid_out"
# if ([int]::Parse($p) -gt 0) {
#     Write-Output "prctl.out pid is $p"
#     as "echo $p > /dev/memcg/prctl/tasks"
# }
cd -
