#! /usr/bin/env pwsh
[CmdletBinding()]
param (
    [Switch]$Lz4
)

aa wait-for-device
aa wait-for-device

if(as cat /proc/swaps | sls "zram0") {
    Write-Debug "zram0 is already enabled"
    # exit
    as "swapoff /dev/block/zram0"
}
as "echo 1 > /sys/block/zram0/reset"
if(-not $Lz4) {
as "echo obja_lz4 > /sys/block/zram0/comp_algorithm"
} else {
as "echo lz4 > /sys/block/zram0/comp_algorithm"
}
as "echo $(2*1024*1024*1024) > /sys/block/zram0/disksize"
as "mkswap /dev/block/zram0"
as "swapon /dev/block/zram0"
aa remount
