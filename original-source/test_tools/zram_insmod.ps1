#! /usr/bin/env pwsh
param(
[switch]$no_remap,
[switch]$sunfish
)

if($sunfish) {
    $TARGET_KSRCDIR = $SUNFISH_KSRCDIR
    $KMOD_PATH = "$TARGET_KSRCDIR/out/android-msm-pixel-4.14/dist/lxr_km_main.ko"
} else {
    $TARGET_KSRCDIR = $GOLDFISH_KSRCDIR
    $KMOD_PATH = "$TARGET_KSRCDIR/out/x86_64/goldfish/mm/lxr_swap_logger_kmod/lxr_km_main.ko"
}

Write-Host (Get-Command aa)
aa wait-for-device
aa push ${KMOD_PATH} /data/local/tmp/lxr_km_main.ko
if($no_remap) {
    # 0, 1 for user compress and user decompress
    as "insmod /data/local/tmp/lxr_km_main.ko enabled_fetures=0,1"
} else {
    # 1,1,0,1,1,0,1 for all features
    as "insmod /data/local/tmp/lxr_km_main.ko enabled_fetures=1,1,0,1,1,0,1"
}

as mknod -m 777 /dev/pp_shm_node c 301 0
as mknod -m 777 /dev/debugmem_node c 303 0
as mknod -m 777 /dev/remap_shm_node c 302 0
as ls -l /dev/pp_shm_node
as ls -l /dev/debugmem_node
as ls -l /dev/remap_shm_node
as "cat /proc/lxr_reset_stats"
as "echo 1 > /proc/sys/kernel/kptr_restrict"
