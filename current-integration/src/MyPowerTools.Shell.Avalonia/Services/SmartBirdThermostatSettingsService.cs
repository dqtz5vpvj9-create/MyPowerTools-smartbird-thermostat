using System.Diagnostics;
using System.Globalization;
using System.Net.Sockets;
using System.Text.Json;
using MyPowerTools.Platform.Abstractions;
using MyPowerTools.Platform.Windows;

namespace MyPowerTools.Shell.Avalonia.Services;

public sealed record SmartBirdThermostatSettings
{
    public string ServiceHost { get; init; } = "127.0.0.1";
    public int ServicePort { get; init; } = 19002;
    public string SmartBirdHost { get; init; } = "0.0.0.0";
    public int SmartBirdPort { get; init; } = 19001;
    public string AdbSerials { get; init; } = "10.33.0.243:5555,192.168.29.79:35559";
    public double LoopSec { get; init; } = 10;
    public double MinOnSec { get; init; } = 60;
    public double MinOffSec { get; init; } = 60;
    public double MarginC { get; init; } = 5;
    public double CondensationGuardC { get; init; } = 3;
    public double MinSurfaceC { get; init; } = 30;
    public double OnSurfaceC { get; init; } = 35;
    public double HysteresisC { get; init; } = 4;
    public double DefaultAmbientC { get; init; } = 28;
    public double DefaultRh { get; init; } = 95;
    public string AmapCity { get; init; } = "310112";
    public double AmapTimeoutSec { get; init; } = 3;
    public double WeatherRefreshSec { get; init; } = 300;
    public bool EnergyServerEnabled { get; init; }
    public string EnergyServerUrl { get; init; } = "http://127.0.0.1:18988";
    public string EnergyBackend { get; init; } = "hid";
    public string UsbMeterSelectorMode { get; init; } = "auto";
    public string UsbMeterSelector { get; init; } = "";
    public bool EnergyAllowUnsafeControl { get; init; }
    public bool NotificationsEnabled { get; init; }
    public string SmtpHost { get; init; } = "smtp.163.com";
    public int SmtpPort { get; init; } = 465;
    public bool SmtpSsl { get; init; } = true;
    public bool SmtpStartTls { get; init; }
    public string SmtpUsername { get; init; } = "";
    public string SmtpSender { get; init; } = "";
    public string SmtpRecipients { get; init; } = "";
    public double NotificationMonitorSec { get; init; } = 30;
    public double NotificationCooldownSec { get; init; } = 1800;
    public bool NotificationSendRecovery { get; init; } = true;
    public int NotificationExpectedMinDevices { get; init; }
}

public sealed record SmartBirdSettingsState(
    SmartBirdThermostatSettings Settings,
    bool HasAmapKey,
    bool HasSmtpPassword,
    string SettingsPath);

public sealed record SmartBirdSettingsApplyResult(
    SmartBirdSettingsState State,
    bool ThermostatTaskRestarted,
    bool EnergyTaskRunning,
    string Message);

public sealed class SmartBirdThermostatSettingsService
{
    public const string AmapSecretName = "amap-key";
    public const string SmtpSecretName = "smtp-password";
    public const string EnergyTaskName = "EnergyServer";

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true
    };

    private readonly string _dataRoot;

    public SmartBirdThermostatSettingsService(string? dataRoot = null)
    {
        _dataRoot = string.IsNullOrWhiteSpace(dataRoot)
            ? Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "MyPowerTools",
                "SmartBird")
            : Path.GetFullPath(Environment.ExpandEnvironmentVariables(dataRoot));
    }

    public string SettingsPath => Path.Combine(_dataRoot, "settings.json");
    public string NotificationConfigPath => Path.Combine(_dataRoot, "notification_config.json");

    public async Task<SmartBirdSettingsState> LoadAsync(CancellationToken cancellationToken = default)
    {
        var settings = new SmartBirdThermostatSettings();
        if (File.Exists(SettingsPath))
        {
            await using var stream = File.OpenRead(SettingsPath);
            settings = await JsonSerializer.DeserializeAsync<SmartBirdThermostatSettings>(
                    stream,
                    JsonOptions,
                    cancellationToken)
                .ConfigureAwait(false) ?? settings;
        }

        var amap = await ReadSecretAsync(AmapSecretName, cancellationToken).ConfigureAwait(false);
        var smtp = await ReadSecretAsync(SmtpSecretName, cancellationToken).ConfigureAwait(false);
        return new SmartBirdSettingsState(settings, !string.IsNullOrEmpty(amap), !string.IsNullOrEmpty(smtp), SettingsPath);
    }

    public async Task<SmartBirdSettingsApplyResult> SaveAndApplyAsync(
        SmartBirdThermostatSettings settings,
        string? amapKey,
        string? smtpPassword,
        CancellationToken cancellationToken = default)
    {
        Validate(settings);
        Directory.CreateDirectory(_dataRoot);
        await WriteJsonAtomicallyAsync(SettingsPath, settings, cancellationToken).ConfigureAwait(false);
        await WriteNotificationConfigAsync(settings, cancellationToken).ConfigureAwait(false);

        if (amapKey is not null)
        {
            await SaveOrDeleteSecretAsync(AmapSecretName, amapKey, cancellationToken).ConfigureAwait(false);
        }
        if (smtpPassword is not null)
        {
            await SaveOrDeleteSecretAsync(SmtpSecretName, smtpPassword, cancellationToken).ConfigureAwait(false);
        }

        var runtime = ResolveRuntimeLayout();
        await RunTaskInstallerAsync(
            runtime.SmartBirdInstaller,
            ["-Mode", "Restart", "-RepoRoot", runtime.SmartBirdRoot, "-DataRoot", _dataRoot],
            cancellationToken).ConfigureAwait(false);

        var energyRunning = false;
        if (settings.EnergyServerEnabled)
        {
            await RunTaskInstallerAsync(
                runtime.EnergyInstaller,
                [
                    "-Mode", "Restart",
                    "-RepoRoot", runtime.SmartBirdRoot,
                    "-DataRoot", _dataRoot
                ],
                cancellationToken).ConfigureAwait(false);
            energyRunning = true;
        }
        else
        {
            await RunTaskInstallerAsync(
                runtime.EnergyInstaller,
                ["-Mode", "Stop", "-RepoRoot", runtime.SmartBirdRoot, "-DataRoot", _dataRoot],
                cancellationToken,
                allowMissingTask: true).ConfigureAwait(false);
        }

        var state = await LoadAsync(cancellationToken).ConfigureAwait(false);
        return new SmartBirdSettingsApplyResult(
            state,
            ThermostatTaskRestarted: true,
            EnergyTaskRunning: energyRunning,
            energyRunning
                ? "设置已保存；温控服务与 Energy Server 已重新注册并启动。"
                : "设置已保存；温控服务已重新注册并启动，Energy Server 已停用。");
    }

    public async Task<string> TestConnectionsAsync(
        SmartBirdThermostatSettings settings,
        string? amapKey,
        string? smtpPassword,
        CancellationToken cancellationToken = default)
    {
        Validate(settings);
        var results = new List<string>();
        var connectHost = settings.SmartBirdHost is "0.0.0.0" or "::" ? "127.0.0.1" : settings.SmartBirdHost;
        results.Add(await TestTcpAsync("SmartBird TCP", connectHost, settings.SmartBirdPort, cancellationToken).ConfigureAwait(false));

        var serials = settings.AdbSerials.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (serials.Length == 0)
        {
            results.Add("ADB：未配置设备");
        }
        else
        {
            var runtime = ResolveRuntimeLayout();
            var online = 0;
            foreach (var serial in serials)
            {
                if (await TestAdbAsync(runtime.AdbPath, serial, cancellationToken).ConfigureAwait(false))
                {
                    online++;
                }
            }
            results.Add($"ADB：{online}/{serials.Length} 台可达");
        }

        if (settings.EnergyServerEnabled)
        {
            results.Add(await TestHttpAsync("Energy Server", settings.EnergyServerUrl, cancellationToken).ConfigureAwait(false));
        }

        var effectiveAmapKey = amapKey ?? await ReadSecretAsync(AmapSecretName, cancellationToken).ConfigureAwait(false);
        results.Add(string.IsNullOrWhiteSpace(effectiveAmapKey) ? "高德天气：未配置密钥" : "高德天气：密钥已配置");

        if (settings.NotificationsEnabled)
        {
            var effectivePassword = smtpPassword ?? await ReadSecretAsync(SmtpSecretName, cancellationToken).ConfigureAwait(false);
            results.Add(string.IsNullOrWhiteSpace(effectivePassword)
                ? "邮件通知：缺少密码"
                : await TestTcpAsync("SMTP", settings.SmtpHost, settings.SmtpPort, cancellationToken).ConfigureAwait(false));
        }

        return string.Join(" · ", results);
    }

    public async Task<string> StartEnergyServerAsync(CancellationToken cancellationToken = default)
    {
        var state = await LoadAsync(cancellationToken).ConfigureAwait(false);
        Validate(state.Settings with { EnergyServerEnabled = true });
        return await ChangeEnergyTaskStateAsync("Start", cancellationToken).ConfigureAwait(false);
    }

    public Task<string> StopEnergyServerAsync(CancellationToken cancellationToken = default) =>
        ChangeEnergyTaskStateAsync("Stop", cancellationToken);

    private async Task<string> ChangeEnergyTaskStateAsync(string mode, CancellationToken cancellationToken)
    {
        var runtime = ResolveRuntimeLayout();
        await RunTaskInstallerAsync(
            runtime.EnergyInstaller,
            ["-Mode", mode, "-RepoRoot", runtime.SmartBirdRoot],
            cancellationToken).ConfigureAwait(false);
        return mode == "Start" ? "Energy Server 已启动。" : "Energy Server 已停止。";
    }

    private async Task SaveOrDeleteSecretAsync(string name, string value, CancellationToken cancellationToken)
    {
        if (!OperatingSystem.IsWindows())
        {
            if (!string.IsNullOrEmpty(value))
            {
                throw new PlatformNotSupportedException("SmartBird 密钥存储需要 Windows Credential Manager。");
            }
            return;
        }
        var secretStore = new WindowsCredentialSecretStore();
        var reference = SecretReference.Create(SmartBirdThermostatToolService.ModuleId, name);
        if (string.IsNullOrEmpty(value))
        {
            await secretStore.DeleteAsync(reference, cancellationToken).ConfigureAwait(false);
            return;
        }
        await secretStore.SaveAsync(SmartBirdThermostatToolService.ModuleId, name, value, cancellationToken).ConfigureAwait(false);
    }

    private static Task<string?> ReadSecretAsync(string name, CancellationToken cancellationToken)
    {
        if (!OperatingSystem.IsWindows())
        {
            return Task.FromResult<string?>(null);
        }
        var secretStore = new WindowsCredentialSecretStore();
        return secretStore.ReadAsync(
            SecretReference.Create(SmartBirdThermostatToolService.ModuleId, name),
            cancellationToken);
    }

    private async Task WriteNotificationConfigAsync(
        SmartBirdThermostatSettings settings,
        CancellationToken cancellationToken)
    {
        var config = new
        {
            enabled = settings.NotificationsEnabled,
            smtp_host = settings.SmtpHost,
            smtp_port = settings.SmtpPort,
            smtp_ssl = settings.SmtpSsl,
            smtp_starttls = settings.SmtpStartTls,
            username = settings.SmtpUsername,
            password = "",
            sender = settings.SmtpSender,
            recipients = settings.SmtpRecipients.Split(
                [',', ';', '\r', '\n'],
                StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries),
            monitor_interval_sec = settings.NotificationMonitorSec,
            cooldown_sec = settings.NotificationCooldownSec,
            send_recovery = settings.NotificationSendRecovery,
            expected_min_devices = settings.NotificationExpectedMinDevices
        };
        await WriteJsonAtomicallyAsync(NotificationConfigPath, config, cancellationToken).ConfigureAwait(false);
    }

    private static async Task WriteJsonAtomicallyAsync<T>(
        string path,
        T value,
        CancellationToken cancellationToken)
    {
        var tempPath = path + ".tmp";
        await using (var stream = File.Create(tempPath))
        {
            await JsonSerializer.SerializeAsync(stream, value, JsonOptions, cancellationToken).ConfigureAwait(false);
        }
        File.Move(tempPath, path, overwrite: true);
    }

    private static void Validate(SmartBirdThermostatSettings settings)
    {
        if (settings.ServicePort is < 1 or > 65535 || settings.SmartBirdPort is < 1 or > 65535 ||
            settings.SmtpPort is < 1 or > 65535)
        {
            throw new InvalidOperationException("端口必须位于 1 到 65535。 ");
        }
        if (string.IsNullOrWhiteSpace(settings.ServiceHost) || string.IsNullOrWhiteSpace(settings.SmartBirdHost))
        {
            throw new InvalidOperationException("服务地址和 SmartBird TCP 地址不能为空。");
        }
        if (settings.ServiceHost is not ("127.0.0.1" or "0.0.0.0"))
        {
            throw new InvalidOperationException(
                "控制台监听地址只允许 127.0.0.1 或 0.0.0.0；嵌入控制台始终通过本机回环地址连接。");
        }
        if (settings.LoopSec <= 0 || settings.MinOnSec < 0 || settings.MinOffSec < 0 ||
            settings.CondensationGuardC < 0 || settings.HysteresisC < 0 ||
            settings.DefaultRh is < 0 or > 100)
        {
            throw new InvalidOperationException("温控周期、最短运行时间、滞回和湿度参数无效。");
        }
        if (!Uri.TryCreate(settings.EnergyServerUrl, UriKind.Absolute, out var energyUri) ||
            energyUri.Scheme is not ("http" or "https"))
        {
            throw new InvalidOperationException("Energy Server URL 必须是有效的 HTTP 或 HTTPS 地址。");
        }
        if (settings.EnergyServerEnabled &&
            (energyUri.Scheme != Uri.UriSchemeHttp ||
             !string.Equals(energyUri.Host, "127.0.0.1", StringComparison.Ordinal) ||
             !string.IsNullOrEmpty(energyUri.UserInfo) ||
             !string.IsNullOrEmpty(energyUri.Query) ||
             !string.IsNullOrEmpty(energyUri.Fragment) ||
             energyUri.AbsolutePath != "/"))
        {
            throw new InvalidOperationException(
                "启用本机 Energy Server 任务时，URL 必须是 http://127.0.0.1:<port>/ 形式。");
        }
        if (settings.EnergyBackend is not ("hid" or "uia"))
        {
            throw new InvalidOperationException("Energy Server 后端必须是 hid 或 uia。");
        }
        if (settings.UsbMeterSelectorMode is not ("auto" or "serial" or "path" or "title"))
        {
            throw new InvalidOperationException("USB Meter 选择方式无效。");
        }
    }

    private static async Task<string> TestTcpAsync(
        string label,
        string host,
        int port,
        CancellationToken cancellationToken)
    {
        try
        {
            using var client = new TcpClient();
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromSeconds(2));
            await client.ConnectAsync(host, port, timeout.Token).ConfigureAwait(false);
            return $"{label}：可达";
        }
        catch (Exception ex) when (ex is SocketException or OperationCanceledException)
        {
            return $"{label}：不可达";
        }
    }

    private static async Task<string> TestHttpAsync(
        string label,
        string baseUrl,
        CancellationToken cancellationToken)
    {
        try
        {
            using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };
            using var response = await client.GetAsync(new Uri(new Uri(baseUrl.TrimEnd('/') + "/"), "handshake"), cancellationToken)
                .ConfigureAwait(false);
            return $"{label}：HTTP {(int)response.StatusCode}";
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            return $"{label}：不可达";
        }
    }

    private static async Task<bool> TestAdbAsync(
        string adbPath,
        string serial,
        CancellationToken cancellationToken)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = adbPath,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        startInfo.ArgumentList.Add("-s");
        startInfo.ArgumentList.Add(serial);
        startInfo.ArgumentList.Add("get-state");
        using var process = Process.Start(startInfo);
        if (process is null)
        {
            return false;
        }
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(3));
        try
        {
            await process.WaitForExitAsync(timeout.Token).ConfigureAwait(false);
            return process.ExitCode == 0 &&
                   (await process.StandardOutput.ReadToEndAsync(timeout.Token).ConfigureAwait(false))
                   .Contains("device", StringComparison.OrdinalIgnoreCase);
        }
        catch (OperationCanceledException)
        {
            try { process.Kill(entireProcessTree: true); } catch { }
            return false;
        }
    }

    private static async Task RunTaskInstallerAsync(
        string scriptPath,
        IReadOnlyList<string> arguments,
        CancellationToken cancellationToken,
        bool allowMissingTask = false)
    {
        if (!File.Exists(scriptPath))
        {
            throw new FileNotFoundException("SmartBird 任务安装脚本不存在。", scriptPath);
        }
        var powershell = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.Windows),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe");
        var startInfo = new ProcessStartInfo
        {
            FileName = powershell,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        foreach (var argument in new[] { "-NoLogo", "-NoProfile", "-NonInteractive", "-File", scriptPath })
        {
            startInfo.ArgumentList.Add(argument);
        }
        foreach (var argument in arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = Process.Start(startInfo) ?? throw new InvalidOperationException("无法启动任务安装脚本。");
        var outputTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var errorTask = process.StandardError.ReadToEndAsync(cancellationToken);
        await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
        var output = await outputTask.ConfigureAwait(false);
        var error = await errorTask.ConfigureAwait(false);
        if (process.ExitCode != 0 && !(allowMissingTask && error.Contains("cannot find", StringComparison.OrdinalIgnoreCase)))
        {
            var detail = string.Join(" ", new[] { error, output }.Where(value => !string.IsNullOrWhiteSpace(value)));
            throw new InvalidOperationException(string.IsNullOrWhiteSpace(detail)
                ? $"任务脚本退出码 {process.ExitCode}。"
                : detail.Trim());
        }
    }

    private static SmartBirdRuntimeLayout ResolveRuntimeLayout()
    {
        var candidates = new List<string>();
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        for (var level = 0; level < 8 && directory is not null; level++, directory = directory.Parent)
        {
            candidates.Add(Path.Combine(directory.FullName, "Runtimes"));
            candidates.Add(Path.Combine(directory.FullName, "artifacts", "release", "win-x64", "Runtimes"));
        }

        foreach (var runtimes in candidates.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            var smartBirdRoot = Path.Combine(runtimes, "SmartBird");
            var pythonPath = Path.Combine(runtimes, "Python312", "python.exe");
            var adbPath = Path.Combine(Path.GetDirectoryName(runtimes)!, "Tools", "AndroidPlatformTools", "adb.exe");
            if (File.Exists(pythonPath) && Directory.Exists(smartBirdRoot))
            {
                return new SmartBirdRuntimeLayout(
                    smartBirdRoot,
                    pythonPath,
                    adbPath,
                    Path.Combine(smartBirdRoot, "scripts", "install-smartbird-thermostat-task.ps1"),
                    Path.Combine(smartBirdRoot, "scripts", "install-energy-server-task.ps1"));
            }
        }

        throw new DirectoryNotFoundException("未找到已发布的 SmartBird 与 Python 运行时。请先执行完整发布。 ");
    }

    private sealed record SmartBirdRuntimeLayout(
        string SmartBirdRoot,
        string PythonPath,
        string AdbPath,
        string SmartBirdInstaller,
        string EnergyInstaller);
}
