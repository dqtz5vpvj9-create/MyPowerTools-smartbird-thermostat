#! /usr/bin/env pwsh
[CmdletBinding()]
param (
    [Switch]$SkipCompile=$True
)
cd /home/lixr/repo/ndkrepo/prctl
# rm -f prctl.out
# /home/lixr/Android/Sdk/ndk/23.0.7599858/toolchains/llvm/prebuilt/linux-x86_64/bin/x86_64-linux-android30-clang++ -static-libstdc++ ./lxr_zram.cc ./lz4o_decompress.c ./prctl_new.cpp -o ./prctl.out
# /home/lixr/Android/Sdk/ndk/23.0.7599858/toolchains/llvm/prebuilt/linux-x86_64/bin/x86_64-linux-android30-clang++ -static-libstdc++ ./datagen.c ./heap.cpp ./stringprintf.cpp ./lxr_zram.cc ./prctl_new.cpp ./lxr_base_utils.cpp -o ./prctl.out
# /home/lixr/Android/Sdk/ndk/23.0.7599858/toolchains/llvm/prebuilt/linux-x86_64/bin/x86_64-linux-android30-clang++ -static-libstdc++ ./datagen.c ./heap.cpp ./stringprintf.cpp ./lxr_zram.cc ./main_test.cpp ./lxr_base_utils.cpp -o ./prctl.out
# Sync time with windows
as "date $(Get-Date -UFormat +%m%d%H%M%Y.%S)" | Out-Null
if($SkipCompile -ne $True) {
    make clean
    make -j
    if (Test-Path "prctl.out") {
        Write-Output "prctl.c compile success"
    }
    else {
        Write-Output "prctl.c compile failed"
        exit
    }
    aa remount
    aa push ./prctl.out /system/bin/prctl.out
    as chmod +x /system/bin/prctl.out
    as mkdir -p /dev/memcg/prctl
}
as mkdir -p /dev/memcg/prctl
as "echo 10M > /dev/memcg/prctl/memory.limit_in_bytes"
Write-Output "Waiting for prctl.pid"
$found_pid = $false
as rm /data/local/tmp/prctl.pid
as touch /data/local/tmp/prctl.pid
as sync
$start_date = Get-Date
while (!$found_pid) {
    $ls_out = (as ls -ll /data/local/tmp/prctl.pid *>&1)
    Write-Debug $ls_out
    if([String]::IsNullOrWhiteSpace($ls_out)) {
        continue
    }
    if ($ls_out | sls "No such file or directory") {
        continue
    }
    if ($ls_out | sls "device 'emulator-5554' not found") {
        exit
    }
    # Get the date from the ls output
    $pattern = "(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}).*prctl\.pid"
    # Get YYYY-MM-DD HH:MM from the ls output
    if ($ls_out -match $pattern) {
        $date = $matches[1]
        # Convert the date to a datetime object
        $date = [datetime]$date
        # if date is later than start date, then we have found the pid
        if ($date -gt $start_date) {
            $found_pid = $true
            # cat the pid file
            $pid_out = (as cat /data/local/tmp/prctl.pid)
            # check if the pid is valid
            if ($pid_out -match "\d+") {
                # $pid_out = $matches[0]
                Write-Output "Found pid: $pid_out"
            }
            else {
                Write-Output "Invalid pid"
            }
        }
        else {
            Write-Debug "$date is not later than $start_date"
        }
    }
}

# Read-Host "Press any key to set memory limit for $pid_out"
# if ([int]::Parse($p) -gt 0) {
#     Write-Output "prctl.out pid is $p"
#     as "echo $p > /dev/memcg/prctl/tasks"
# }
as "echo $pid_out > /dev/memcg/prctl/tasks"
as cat /dev/memcg/prctl/tasks
cd -

# $port = 11655
# $endpoint = new-object System.Net.IPEndPoint ([system.net.ipaddress]::any, $port)
# $listener = new-object System.Net.Sockets.TcpListener $endpoint
# $listener.start()
# $client = $listener.AcceptTcpClient()
# $Stream = $client.GetStream()
# $Response = [System.Text.Encoding]::UTF8.GetBytes("$pid_out")
# $Stream.Write($Response, 0, $Response.Length)
# $client.Close()
# $listener.Stop()
