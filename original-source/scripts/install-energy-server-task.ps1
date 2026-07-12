[CmdletBinding()]
param(
    [ValidateSet("Install", "Status", "Start", "Stop", "Restart", "Uninstall")]
    [string]$Mode = "Status",

    [string]$TaskName = "EnergyServer",
    [string]$RepoRoot = "",
    [string]$PythonPath = "",
    [string]$DataRoot = "",
    [string]$SettingsFile = "",
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
    param([string]$Name)
    $task = Get-TaskOrNull -Name $Name
    $listeners = @()
    try {
        $listeners = Get-NetTCPConnection -LocalPort 18988 -State Listen -ErrorAction Stop |
            Select-Object LocalAddress, LocalPort, OwningProcess
    } catch {
        $listeners = @()
    }

    if ($null -eq $task) {
        [ordered]@{
            task_name = $Name
            installed = $false
            http_url = "http://127.0.0.1:18988"
            listeners = $listeners
        } | ConvertTo-Json -Depth 4
        return
    }

    $info = Get-ScheduledTaskInfo -TaskName $Name
    [ordered]@{
        task_name = $Name
        installed = $true
        state = $task.State.ToString()
        last_run_time = $info.LastRunTime
        last_task_result = $info.LastTaskResult
        next_run_time = $info.NextRunTime
        http_url = "http://127.0.0.1:18988"
        listeners = $listeners
    } | ConvertTo-Json -Depth 4
}

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RepoRoot = Join-Path $ScriptDir ".."
}
$RepoRoot = (Resolve-Path $RepoRoot).Path
$EntryScript = Join-Path $RepoRoot "test_tools\energy_server_task.py"
$ConhostPath = Join-Path $env:WINDIR "System32\conhost.exe"
if ([string]::IsNullOrWhiteSpace($DataRoot)) {
    $LocalAppData = [Environment]::GetFolderPath("LocalApplicationData")
    $DataRoot = Join-Path $LocalAppData "MyPowerTools\SmartBird"
}
$DataRoot = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($DataRoot))
if ([string]::IsNullOrWhiteSpace($SettingsFile)) {
    $SettingsFile = Join-Path $DataRoot "settings.json"
}
$SettingsFile = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($SettingsFile))
$LogDir = Join-Path $DataRoot "logs"
$LogFile = Join-Path $LogDir "energy_server.log"

function Stop-EnergyServerTaskProcesses {
    $entryPattern = [regex]::Escape($EntryScript)
    $targets = Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -match $entryPattern } |
        Select-Object ProcessId, ParentProcessId, CommandLine
    foreach ($target in $targets) {
        Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

switch ($Mode) {
    "Install" {
        if (-not (Test-Path $EntryScript)) {
            throw "Energy server task entrypoint not found: $EntryScript"
        }
        if (-not (Test-Path $ConhostPath)) {
            throw "conhost.exe not found: $ConhostPath"
        }
        if (-not (Test-Path -LiteralPath $SettingsFile -PathType Leaf)) {
            throw "SmartBird settings file not found: $SettingsFile"
        }
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
        $ResolvedPython = Get-PythonPath

        & $ConhostPath --headless "$env:WINDIR\System32\cmd.exe" /c exit 0
        if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            throw "conhost.exe --headless probe failed with exit code $LASTEXITCODE"
        }

        $actionArgs = "--headless " + @(
            (Quote-NativeArg $ResolvedPython),
            (Quote-NativeArg $EntryScript),
            "--settings-file", (Quote-NativeArg $SettingsFile),
            "--data-root", (Quote-NativeArg $DataRoot),
            "--log-file", (Quote-NativeArg $LogFile)
        ) -join " "

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
            Stop-EnergyServerTaskProcesses
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }

        $registerArgs = @{
            TaskName = $TaskName
            Action = $action
            Trigger = $trigger
            Settings = $settings
            Principal = $principal
            Description = "Energy server with HID safe-readonly virtual sessions and Smart-Bird thermal forwarding"
        }
        Register-ScheduledTask @registerArgs | Out-Null

        if ($StartAfterInstall) {
            Start-ScheduledTask -TaskName $TaskName
        }
        Show-Status -Name $TaskName
    }

    "Status" {
        Show-Status -Name $TaskName
    }

    "Start" {
        if ($null -eq (Get-TaskOrNull -Name $TaskName)) {
            throw "Energy Server task is not installed. Repair the MyPowerTools installation first."
        }
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 2
        Show-Status -Name $TaskName
    }

    "Stop" {
        if ($null -eq (Get-TaskOrNull -Name $TaskName)) {
            Show-Status -Name $TaskName
            break
        }
        Stop-ScheduledTask -TaskName $TaskName
        Stop-EnergyServerTaskProcesses
        Start-Sleep -Seconds 2
        Show-Status -Name $TaskName
    }

    "Restart" {
        if ($null -eq (Get-TaskOrNull -Name $TaskName)) {
            throw "Energy Server task is not installed. Repair the MyPowerTools installation first."
        }
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Stop-EnergyServerTaskProcesses
        Start-Sleep -Seconds 2
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 2
        Show-Status -Name $TaskName
    }

    "Uninstall" {
        $task = Get-TaskOrNull -Name $TaskName
        if ($null -ne $task) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Stop-EnergyServerTaskProcesses
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        Show-Status -Name $TaskName
    }
}
