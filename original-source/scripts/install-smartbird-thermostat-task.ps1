[CmdletBinding()]
param(
    [ValidateSet("Install", "Status", "Start", "Stop", "Restart", "Uninstall")]
    [string]$Mode = "Status",

    [string]$TaskName = "SmartBirdThermostat",
    [string]$RepoRoot = "",
    [string]$PythonPath = "",
    [string]$DataRoot = "",
    [string]$SettingsFile = "",

    [string]$ServiceHost = "127.0.0.1",
    [int]$ServicePort = 19002,
    [string]$SmartBirdHost = "0.0.0.0",
    [int]$SmartBirdPort = 19001,
    [string]$AdbSerial = "10.33.0.243:5555,192.168.29.79:35559",

    [double]$LoopSec = 10.0,
    [double]$MinOnSec = 60.0,
    [double]$MinOffSec = 60.0,
    [double]$MarginC = 5.0,
    [double]$CondensationGuardC = 3.0,
    [double]$MinSurfaceC = 30.0,
    [double]$OnSurfaceC = 35.0,
    [double]$HysteresisC = 4.0,
    [double]$DefaultAmbientC = 28.0,
    [double]$DefaultRh = 95.0,
    [string]$AmapCity = "310112",

    [switch]$StartAfterInstall
)

$ErrorActionPreference = "Stop"

function Quote-NativeArg {
    param([Parameter(Mandatory = $true)][string]$Value)
    if ($Value.Length -eq 0) {
        return '""'
    }
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Get-PythonPath {
    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
        return (Resolve-Path $PythonPath).Path
    }
    $cmd = Get-Command python -ErrorAction Stop
    return $cmd.Source
}

function Get-TaskOrNull {
    param([string]$Name)
    return Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
}

function Show-Status {
    param(
        [string]$Name,
        [int]$Port
    )
    $task = Get-TaskOrNull -Name $Name
    if ($null -eq $task) {
        [ordered]@{
            task_name = $Name
            installed = $false
            http_control_url = "http://${ServiceHost}:${Port}"
        } | ConvertTo-Json -Depth 4
        return
    }

    $info = Get-ScheduledTaskInfo -TaskName $Name
    $listeners = @()
    try {
        $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object LocalAddress, LocalPort, OwningProcess
    } catch {
        $listeners = @()
    }

    [ordered]@{
        task_name = $Name
        installed = $true
        state = $task.State.ToString()
        last_run_time = $info.LastRunTime
        last_task_result = $info.LastTaskResult
        next_run_time = $info.NextRunTime
        http_control_url = "http://${ServiceHost}:${Port}"
        listeners = $listeners
    } | ConvertTo-Json -Depth 4
}

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RepoRoot = Join-Path $ScriptDir ".."
}
$RepoRoot = (Resolve-Path $RepoRoot).Path
$DataRoot = [Environment]::ExpandEnvironmentVariables($DataRoot)
if ([string]::IsNullOrWhiteSpace($DataRoot)) {
    $LocalAppData = [Environment]::GetFolderPath("LocalApplicationData")
    if ([string]::IsNullOrWhiteSpace($LocalAppData)) {
        throw "LocalApplicationData is unavailable for the current user. Pass -DataRoot explicitly."
    }
    $DataRoot = Join-Path $LocalAppData "MyPowerTools\SmartBird"
}
$DataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$ServiceScript = Join-Path $RepoRoot "test_tools\smartbird_thermostat_service.py"
$LogDir = Join-Path $DataRoot "logs"
$LogFile = Join-Path $LogDir "smartbird_thermostat_service.log"
$ConhostPath = Join-Path $env:WINDIR "System32\conhost.exe"
if ([string]::IsNullOrWhiteSpace($SettingsFile)) {
    $SettingsFile = Join-Path $DataRoot "settings.json"
}
$SettingsFile = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($SettingsFile))
$TaskEntryScript = Join-Path $RepoRoot "test_tools\smartbird_thermostat_task.py"

function Stop-SmartBirdTaskProcesses {
    $servicePattern = [regex]::Escape($ServiceScript)
    $taskEntryPattern = [regex]::Escape($TaskEntryScript)
    $targets = Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -match $servicePattern -or $_.CommandLine -match $taskEntryPattern } |
        Select-Object ProcessId, ParentProcessId, CommandLine
    foreach ($target in $targets) {
        Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

switch ($Mode) {
    "Install" {
        if (-not (Test-Path $ServiceScript)) {
            throw "Service script not found: $ServiceScript"
        }
        if (-not (Test-Path $TaskEntryScript)) {
            throw "Task entry script not found: $TaskEntryScript"
        }
        if (-not (Test-Path $ConhostPath)) {
            throw "conhost.exe not found: $ConhostPath"
        }
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
        if (-not (Test-Path -LiteralPath $SettingsFile -PathType Leaf)) {
            $defaultSettings = [ordered]@{
                serviceHost = $ServiceHost
                servicePort = $ServicePort
                smartBirdHost = $SmartBirdHost
                smartBirdPort = $SmartBirdPort
                adbSerials = $AdbSerial
                loopSec = $LoopSec
                minOnSec = $MinOnSec
                minOffSec = $MinOffSec
                marginC = $MarginC
                condensationGuardC = $CondensationGuardC
                minSurfaceC = $MinSurfaceC
                onSurfaceC = $OnSurfaceC
                hysteresisC = $HysteresisC
                defaultAmbientC = $DefaultAmbientC
                defaultRh = $DefaultRh
                amapCity = $AmapCity
                amapTimeoutSec = 3.0
                weatherRefreshSec = 300.0
                energyServerEnabled = $false
                energyServerUrl = "http://127.0.0.1:18988"
                energyBackend = "hid"
                usbMeterSelectorMode = "auto"
                usbMeterSelector = ""
                energyAllowUnsafeControl = $false
                notificationsEnabled = $false
                smtpHost = "smtp.163.com"
                smtpPort = 465
                smtpSsl = $true
                smtpStartTls = $false
                smtpUsername = ""
                smtpSender = ""
                smtpRecipients = ""
                notificationMonitorSec = 30.0
                notificationCooldownSec = 1800.0
                notificationSendRecovery = $true
                notificationExpectedMinDevices = 0
            }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $SettingsFile) | Out-Null
            $settingsJson = $defaultSettings | ConvertTo-Json -Depth 4
            [System.IO.File]::WriteAllText($SettingsFile, $settingsJson, [System.Text.UTF8Encoding]::new($false))
        }
        $ResolvedPython = Get-PythonPath

        & $ConhostPath --headless "$env:WINDIR\System32\cmd.exe" /c exit 0
        if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            throw "conhost.exe --headless probe failed with exit code $LASTEXITCODE"
        }

        $serviceArgs = @(
            (Quote-NativeArg $ResolvedPython),
            (Quote-NativeArg $TaskEntryScript),
            "--settings-file", (Quote-NativeArg $SettingsFile),
            "--data-root", (Quote-NativeArg $DataRoot),
            "--log-file", (Quote-NativeArg $LogFile)
        )
        $actionArgs = "--headless " + ($serviceArgs -join " ")

        $action = New-ScheduledTaskAction `
            -Execute $ConhostPath `
            -Argument $actionArgs `
            -WorkingDirectory $RepoRoot
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
            -MultipleInstances IgnoreNew `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1)
        $principal = New-ScheduledTaskPrincipal `
            -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
            -LogonType Interactive `
            -RunLevel Limited

        $existing = Get-TaskOrNull -Name $TaskName
        if ($null -ne $existing) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Stop-SmartBirdTaskProcesses
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        $registerArgs = @{
            TaskName = $TaskName
            Action = $action
            Trigger = $trigger
            Settings = $settings
            Description = "Smart-Bird dew-point thermostat service"
        }
        try {
            Register-ScheduledTask @registerArgs -Principal $principal | Out-Null
        } catch {
            Register-ScheduledTask @registerArgs | Out-Null
        }

        if ($StartAfterInstall) {
            Start-ScheduledTask -TaskName $TaskName
        }
        Show-Status -Name $TaskName -Port $ServicePort
    }

    "Status" {
        Show-Status -Name $TaskName -Port $ServicePort
    }

    "Start" {
        Start-ScheduledTask -TaskName $TaskName
        Show-Status -Name $TaskName -Port $ServicePort
    }

    "Stop" {
        Stop-ScheduledTask -TaskName $TaskName
        Stop-SmartBirdTaskProcesses
        Show-Status -Name $TaskName -Port $ServicePort
    }

    "Restart" {
        if ($null -eq (Get-TaskOrNull -Name $TaskName)) {
            throw "SmartBird Thermostat task is not installed. Repair the MyPowerTools installation first."
        }
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Stop-SmartBirdTaskProcesses
        Start-Sleep -Seconds 2
        Start-ScheduledTask -TaskName $TaskName
        Show-Status -Name $TaskName -Port $ServicePort
    }

    "Uninstall" {
        $task = Get-TaskOrNull -Name $TaskName
        if ($null -ne $task) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Stop-SmartBirdTaskProcesses
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        Show-Status -Name $TaskName -Port $ServicePort
    }
}
