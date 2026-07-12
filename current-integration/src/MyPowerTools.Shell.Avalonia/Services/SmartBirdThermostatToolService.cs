using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace MyPowerTools.Shell.Avalonia.Services;

public sealed class SmartBirdThermostatToolService : IDisposable
{
    public const string ModuleId = "smartbird-thermostat";
    public const string ScheduledTaskName = "SmartBirdThermostat";
    public static readonly Uri DefaultBaseUri = new("http://127.0.0.1:19002/");

    private readonly HttpClient _httpClient;
    private readonly bool _ownsHttpClient;
    private readonly SmartBirdThermostatSettingsService _settingsService;

    public SmartBirdThermostatToolService(
        HttpClient? httpClient = null,
        SmartBirdThermostatSettingsService? settingsService = null)
    {
        _httpClient = httpClient ?? CreateLoopbackHttpClient();
        _ownsHttpClient = httpClient is null;
        _settingsService = settingsService ?? new SmartBirdThermostatSettingsService();
    }

    public async Task<SmartBirdThermostatSnapshot> LoadAsync(CancellationToken cancellationToken = default)
    {
        var baseUri = await ResolveDashboardUriAsync(cancellationToken).ConfigureAwait(false);
        return await ProbeAsync(baseUri, cancellationToken).ConfigureAwait(false);
    }

    public async Task<SmartBirdThermostatSnapshot> StartScheduledServiceAsync(
        CancellationToken cancellationToken = default)
    {
        var baseUri = await ResolveDashboardUriAsync(cancellationToken).ConfigureAwait(false);
        if (!OperatingSystem.IsWindows())
        {
            return SmartBirdThermostatSnapshot.Offline(
                baseUri,
                "当前系统无法启动 SmartBird 后台任务。",
                "请启动 SmartBird 服务后重试。");
        }

        var windowsDirectory = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        var schtasksPath = Path.Combine(windowsDirectory, "System32", "schtasks.exe");
        if (!File.Exists(schtasksPath))
        {
            return SmartBirdThermostatSnapshot.Offline(
                baseUri,
                "找不到 Windows 任务计划程序。",
                "请在系统中启动 SmartBirdThermostat 任务。");
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = schtasksPath,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        startInfo.ArgumentList.Add("/Run");
        startInfo.ArgumentList.Add("/TN");
        startInfo.ArgumentList.Add(ScheduledTaskName);

        using var process = Process.Start(startInfo);
        if (process is null)
        {
            return SmartBirdThermostatSnapshot.Offline(
                baseUri,
                "后台任务启动失败。",
                $"请检查任务计划程序中的 {ScheduledTaskName}。");
        }

        var standardOutputTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var standardErrorTask = process.StandardError.ReadToEndAsync(cancellationToken);
        await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
        var standardOutput = await standardOutputTask.ConfigureAwait(false);
        var standardError = await standardErrorTask.ConfigureAwait(false);
        if (process.ExitCode != 0)
        {
            var detail = FirstUsefulLine(standardError, standardOutput);
            return SmartBirdThermostatSnapshot.Offline(
                baseUri,
                "后台任务启动失败。",
                string.IsNullOrWhiteSpace(detail)
                    ? $"请检查任务计划程序中的 {ScheduledTaskName}。"
                    : detail);
        }

        SmartBirdThermostatSnapshot? lastSnapshot = null;
        for (var attempt = 0; attempt < 12; attempt++)
        {
            await Task.Delay(TimeSpan.FromMilliseconds(400), cancellationToken).ConfigureAwait(false);
            lastSnapshot = await LoadAsync(cancellationToken).ConfigureAwait(false);
            if (lastSnapshot.IsOnline)
            {
                return lastSnapshot;
            }
        }

        return (lastSnapshot ?? SmartBirdThermostatSnapshot.Offline(
            baseUri,
            "后台任务已启动，服务仍在初始化。",
            "请稍后刷新页面。")) with
        {
            StatusTitle = "后台任务已启动，服务仍在初始化。",
            StatusDetail = "请稍后刷新页面。"
        };
    }

    public static Uri NormalizeBaseUri(Uri candidate)
    {
        if (!IsSupportedDashboardOrigin(candidate))
        {
            return DefaultBaseUri;
        }
        return new UriBuilder(Uri.UriSchemeHttp, "127.0.0.1", candidate.Port, "/").Uri;
    }

    public static bool IsDashboardOrigin(Uri? uri)
    {
        return uri is { IsAbsoluteUri: true } &&
               string.Equals(uri.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase) &&
               string.Equals(uri.Host, "127.0.0.1", StringComparison.Ordinal) &&
               uri.Port == 19002 &&
               string.IsNullOrEmpty(uri.UserInfo);
    }

    public static bool IsSupportedDashboardOrigin(Uri? uri)
    {
        return uri is { IsAbsoluteUri: true } &&
               string.Equals(uri.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase) &&
               string.Equals(uri.Host, "127.0.0.1", StringComparison.Ordinal) &&
               uri.Port is >= 1 and <= 65535 &&
               string.IsNullOrEmpty(uri.UserInfo);
    }

    public static bool HasSameDashboardOrigin(Uri allowed, Uri target)
    {
        return IsSupportedDashboardOrigin(allowed) &&
               IsSupportedDashboardOrigin(target) &&
               allowed.Port == target.Port;
    }

    public void Dispose()
    {
        if (_ownsHttpClient)
        {
            _httpClient.Dispose();
        }
    }

    private async Task<SmartBirdThermostatSnapshot> ProbeAsync(
        Uri baseUri,
        CancellationToken cancellationToken)
    {
        var statusUri = new Uri(baseUri, "api/status");
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(3));

        try
        {
            using var response = await _httpClient.GetAsync(
                statusUri,
                HttpCompletionOption.ResponseHeadersRead,
                timeout.Token).ConfigureAwait(false);
            if (response.RequestMessage?.RequestUri is { } finalUri &&
                !HasSameDashboardOrigin(baseUri, finalUri))
            {
                return SmartBirdThermostatSnapshot.Offline(
                    baseUri,
                    "SmartBird 拒绝了跨源状态响应。",
                    $"状态探活仅允许访问已配置的本机端点 {baseUri.Host}:{baseUri.Port}。",
                    "cross-origin-response");
            }
            if (!response.IsSuccessStatusCode)
            {
                return SmartBirdThermostatSnapshot.Offline(
                    baseUri,
                    "SmartBird 服务暂时不可用。",
                    $"状态接口返回 {(int)response.StatusCode} {response.ReasonPhrase}。",
                    response.StatusCode.ToString());
            }

            await using var stream = await response.Content.ReadAsStreamAsync(timeout.Token).ConfigureAwait(false);
            var status = await JsonNode.ParseAsync(stream, cancellationToken: timeout.Token).ConfigureAwait(false)
                as JsonObject;
            if (status is null)
            {
                return SmartBirdThermostatSnapshot.Offline(
                    baseUri,
                    "SmartBird 返回了无法识别的状态。",
                    "请刷新页面；问题持续时可在浏览器中打开控制台。",
                    "invalid-status");
            }

            return ParseOnlineSnapshot(baseUri, status);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return SmartBirdThermostatSnapshot.Offline(
                baseUri,
                "SmartBird 服务响应超时。",
                $"未能在 3 秒内连接 {baseUri.Host}:{baseUri.Port}。",
                "timeout");
        }
        catch (HttpRequestException ex)
        {
            return SmartBirdThermostatSnapshot.Offline(
                baseUri,
                "SmartBird 服务尚未连接。",
                FriendlyConnectionError(ex, baseUri),
                "connection-failed");
        }
        catch (JsonException)
        {
            return SmartBirdThermostatSnapshot.Offline(
                baseUri,
                "SmartBird 返回了无法识别的状态。",
                "状态接口没有返回有效 JSON。",
                "invalid-json");
        }
    }

    private async Task<Uri> ResolveDashboardUriAsync(CancellationToken cancellationToken)
    {
        var state = await _settingsService.LoadAsync(cancellationToken).ConfigureAwait(false);
        var host = state.Settings.ServiceHost == "0.0.0.0"
            ? "127.0.0.1"
            : state.Settings.ServiceHost;
        var candidate = new UriBuilder(
            Uri.UriSchemeHttp,
            host,
            state.Settings.ServicePort,
            "/").Uri;
        if (!IsSupportedDashboardOrigin(candidate))
        {
            throw new InvalidOperationException(
                "SmartBird 控制台端点必须使用本机 127.0.0.1 和有效端口。");
        }
        return candidate;
    }

    private static SmartBirdThermostatSnapshot ParseOnlineSnapshot(Uri baseUri, JsonObject status)
    {
        var decision = status["last_decision"] as JsonObject;
        var switchStatus = status["switch"] as JsonObject;
        var mode = ReadString(status, "mode", "unknown");
        var lastKey = ReadNullableInt(status, "last_key") ??
                      ReadNullableInt(switchStatus, "reported_key") ??
                      ReadNullableInt(switchStatus, "desired_key");
        var surfaceC = ReadNullableDouble(decision, "surface_c");
        var clientCount = ReadNullableInt(switchStatus, "client_count") ?? 0;
        var detailParts = new List<string>();
        if (surfaceC is not null)
        {
            detailParts.Add($"表面 {surfaceC.Value.ToString("0.0", CultureInfo.InvariantCulture)} °C");
        }
        detailParts.Add(lastKey switch
        {
            1 => "制冷已开启",
            0 => "制冷已关闭",
            _ => "制冷状态待确认"
        });
        detailParts.Add(clientCount == 1 ? "1 台 SmartBird 设备在线" : $"{clientCount} 台 SmartBird 设备在线");

        return new SmartBirdThermostatSnapshot(
            baseUri,
            IsOnline: true,
            Mode: mode,
            CoolingEnabled: lastKey switch { 1 => true, 0 => false, _ => null },
            ClientCount: clientCount,
            SurfaceC: surfaceC,
            StatusTitle: "SmartBird 服务已连接",
            StatusDetail: string.Join(" · ", detailParts),
            CheckedAt: DateTimeOffset.Now,
            ErrorCode: "");
    }

    private static string FriendlyConnectionError(HttpRequestException exception, Uri baseUri)
    {
        return exception.StatusCode switch
        {
            HttpStatusCode.Unauthorized => "服务要求登录，请在浏览器中完成认证后重试。",
            HttpStatusCode.Forbidden => "当前账户无权访问 SmartBird 控制台。",
            _ => $"无法连接 {baseUri.Host}:{baseUri.Port}。可启动后台服务后重试。"
        };
    }

    private static string FirstUsefulLine(params string[] values)
    {
        return values
            .SelectMany(value => value.Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries))
            .Select(value => value.Trim())
            .FirstOrDefault(value => value.Length > 0) ?? "";
    }

    private static HttpClient CreateLoopbackHttpClient()
    {
        return new HttpClient(new SocketsHttpHandler
        {
            AllowAutoRedirect = false,
            UseCookies = false,
            UseProxy = false,
            ConnectTimeout = TimeSpan.FromSeconds(2)
        });
    }

    private static string ReadString(JsonObject? source, string name, string fallback = "")
    {
        if (source?[name] is not JsonValue value)
        {
            return fallback;
        }

        try
        {
            return value.GetValue<string>();
        }
        catch (InvalidOperationException)
        {
            return fallback;
        }
    }

    private static int? ReadNullableInt(JsonObject? source, string name)
    {
        if (source?[name] is not JsonValue value)
        {
            return null;
        }

        if (value.TryGetValue<int>(out var intValue))
        {
            return intValue;
        }
        if (value.TryGetValue<long>(out var longValue) && longValue is >= int.MinValue and <= int.MaxValue)
        {
            return (int)longValue;
        }
        return null;
    }

    private static double? ReadNullableDouble(JsonObject? source, string name)
    {
        if (source?[name] is not JsonValue value)
        {
            return null;
        }

        if (value.TryGetValue<double>(out var doubleValue))
        {
            return doubleValue;
        }
        if (value.TryGetValue<decimal>(out var decimalValue))
        {
            return (double)decimalValue;
        }
        return null;
    }
}

public sealed record SmartBirdThermostatSnapshot(
    Uri DashboardUri,
    bool IsOnline,
    string Mode,
    bool? CoolingEnabled,
    int ClientCount,
    double? SurfaceC,
    string StatusTitle,
    string StatusDetail,
    DateTimeOffset CheckedAt,
    string ErrorCode)
{
    public static SmartBirdThermostatSnapshot Offline(
        Uri dashboardUri,
        string title,
        string detail,
        string errorCode = "offline")
    {
        return new SmartBirdThermostatSnapshot(
            SmartBirdThermostatToolService.NormalizeBaseUri(dashboardUri),
            IsOnline: false,
            Mode: "unknown",
            CoolingEnabled: null,
            ClientCount: 0,
            SurfaceC: null,
            StatusTitle: title,
            StatusDetail: detail,
            CheckedAt: DateTimeOffset.Now,
            ErrorCode: errorCode);
    }
}
