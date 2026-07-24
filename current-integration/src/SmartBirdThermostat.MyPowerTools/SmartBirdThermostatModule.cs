using System.ComponentModel;
using System.Diagnostics;
using System.Net;
using System.Net.Http.Json;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using MyPowerTools.Protocol;
using MyPowerTools.Abstractions;

namespace SmartBirdThermostat.MyPowerTools;

public sealed class SmartBirdThermostatModule : IMptModule
{
    public const string CanonicalBaseUrl = "http://127.0.0.1:19002";
    public const string CanonicalScheduledTaskName = "SmartBirdThermostat";

    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };
    private static readonly Regex DeviceEndpointPattern = new(
        @"(?<![\d.])(?<host>(?:\d{1,3}\.){3}\d{1,3}):(?<port>\d{1,5})(?!\d)",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private static readonly Regex AdbSerialPropertyPattern = new(
        "\"adb_serial\"\\s*:\\s*\"(?<value>[^\"]*)\"",
        RegexOptions.Compiled | RegexOptions.CultureInvariant | RegexOptions.IgnoreCase);

    private readonly HttpClient _httpClient;
    private ModuleContext? _context;
    private SmartBirdStore? _store;

    public SmartBirdThermostatModule()
        : this(new HttpClient { Timeout = TimeSpan.FromMilliseconds(1400) })
    {
    }

    internal SmartBirdThermostatModule(HttpClient httpClient)
    {
        _httpClient = httpClient;
    }

    public string Id => "smartbird-thermostat";
    public string PackageId => "smartbird-thermostat";
    public Version Version => new(0, 2, 0);

    private ModuleContext Context => _context ?? throw new InvalidOperationException("SmartBird Thermostat was not initialized.");
    private SmartBirdStore Store => _store ?? throw new InvalidOperationException("SmartBird Thermostat was not initialized.");

    public ValueTask<InitializeResult> InitializeAsync(ModuleContext context, CancellationToken cancellationToken)
    {
        _context = context;
        Directory.CreateDirectory(context.DataDirectory);
        Directory.CreateDirectory(context.CacheDirectory);
        Directory.CreateDirectory(context.LogDirectory);
        _store = new SmartBirdStore(Path.Combine(context.DataDirectory, "smartbird-settings.json"));
        Store.EnsureDefaults();
        return ValueTask.FromResult(new InitializeResult(true, context.ProtocolVersion, ["lifecycle", "status", "commands", "settings", "logs", "dashboardCard", "detailPage", "notifications"]));
    }

    public async ValueTask<ModuleStatusSnapshot> GetStatusAsync(CancellationToken cancellationToken)
    {
        var options = Store.Load();
        var payload = await BuildStatusPayloadAsync(options, cancellationToken);
        var checks = ReadChecks(payload);
        var requiredOk = checks.Where(check => IsRequired(check.Id)).All(check => check.Ok);
        var state = requiredOk ? "running" : "degraded";
        var summary = requiredOk
            ? "SmartBird service and required hardware dependencies are reachable."
            : SummarizeDegraded(checks);
        return new ModuleStatusSnapshot(Id, state, summary, DateTimeOffset.UtcNow, checks, 0);
    }

    public ValueTask<IReadOnlyList<MptCommandDescriptor>> ListCommandsAsync(CancellationToken cancellationToken)
    {
        var facadeParameters = FacadeParameters();
        IReadOnlyList<MptCommandDescriptor> commands =
        [
            Command("smartbird-thermostat.status.fetch", "Fetch thermostat status", "Queries status and dependency health through the SmartBird facade", parameters: facadeParameters),
            Command("smartbird-thermostat.status.summary", "Summarize thermostat status", "Reports SmartBird service, Energy Server, and ADB readiness", parameters: facadeParameters),
            Command("smartbird-thermostat.events.list", "List thermostat events", "Reads recent thermostat events or returns an actionable degraded event source", parameters: facadeParameters),
            Command("smartbird-thermostat.logs.summary", "Summarize thermostat logs", "Reads the original scheduled-task service log and Runner-managed module logs", parameters: facadeParameters),
            Command("smartbird-thermostat.config.get", "Read thermostat config", "Reads the local module policy and source-derived runtime configuration", parameters: facadeParameters),
            Command("smartbird-thermostat.config.save", "Save thermostat config", "Validates and persists SmartBird facade policy settings", parameters: ConfigSaveParameters()),
            Command("smartbird-thermostat.hardware.diagnostics", "Check thermostat hardware dependencies", "Checks source-backed Energy Server and ADB dependency readiness", parameters: facadeParameters),
            Command("smartbird-thermostat.self-test", "Run thermostat facade self-test", "Verifies paths, settings schema, endpoints, and redaction"),
            Command("smartbird-thermostat.service.restart", "Restart thermostat service", "Restarts the current-user SmartBird scheduled task", dangerLevel: "medium", parameters: RestartParameters())
        ];
        return ValueTask.FromResult(commands);
    }

    public async ValueTask<CommandExecutionResult> ExecuteCommandAsync(CommandRequest request, CancellationToken cancellationToken)
    {
        return request.CommandId switch
        {
            "smartbird-thermostat.status.fetch" or "smartbird-thermostat.status.summary" => await StatusSummaryAsync(request, cancellationToken),
            "smartbird-thermostat.events.list" => await EventsListAsync(request, cancellationToken),
            "smartbird-thermostat.logs.summary" => LogsSummary(request),
            "smartbird-thermostat.config.get" => ConfigGet(request),
            "smartbird-thermostat.config.save" => ConfigSave(request),
            "smartbird-thermostat.hardware.diagnostics" => await HardwareDiagnosticsAsync(request, cancellationToken),
            "smartbird-thermostat.self-test" => SelfTest(request),
            "smartbird-thermostat.service.restart" => await RestartScheduledTaskAsync(request, cancellationToken),
            _ => Failed(request, MptErrorCodes.NotFound, $"Command '{request.CommandId}' is not implemented by SmartBird Thermostat.")
        };
    }

    public async IAsyncEnumerable<MptModuleEvent> SubscribeEventsAsync(EventCursor cursor, [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        if (cursor.LastEventSeq >= 1)
        {
            yield break;
        }

        var options = Store.Load();
        var payload = await BuildStatusPayloadAsync(options, cancellationToken);
        var checks = ReadChecks(payload);
        var requiredOk = checks.Where(check => IsRequired(check.Id)).All(check => check.Ok);
        yield return new MptModuleEvent(
            Id,
            1,
            requiredOk ? "policy.triggered" : "hardware.missing",
            DateTimeOffset.UtcNow,
            new JsonObject
            {
                ["title"] = "SmartBird hardware policy",
                ["message"] = requiredOk ? "SmartBird required hardware checks are reachable." : SummarizeDegraded(checks),
                ["targetTemperatureC"] = options.TargetTemperatureC,
                ["requiredOk"] = requiredOk,
                ["checkCount"] = checks.Count
            });
    }

    public ValueTask<SettingsSchemaDocument> GetSettingsSchemaAsync(CancellationToken cancellationToken)
    {
        return ValueTask.FromResult(new SettingsSchemaDocument(Id, $$"""
        {
          "type": "object",
          "properties": {
            "baseUrl": {
              "type": "string",
              "const": "{{CanonicalBaseUrl}}",
              "default": "{{CanonicalBaseUrl}}",
              "readOnly": true
            },
            "statusPath": { "type": "string", "default": "/api/status" },
            "eventsPath": { "type": "string", "default": "/api/events" },
            "serviceLogPath": { "type": "string", "default": "" },
            "scheduledTaskName": {
              "type": "string",
              "const": "{{CanonicalScheduledTaskName}}",
              "default": "{{CanonicalScheduledTaskName}}",
              "readOnly": true
            },
            "energyServerBaseUrl": { "type": "string", "default": "http://127.0.0.1:18988" },
            "energyStatusPath": { "type": "string", "default": "/api/energy/status" },
            "adbPath": { "type": "string", "default": "adb" },
            "targetTemperatureC": { "type": "number", "minimum": 0, "maximum": 120, "default": 45 },
            "pollIntervalSeconds": { "type": "integer", "minimum": 5, "maximum": 3600, "default": 30 },
            "eventLimit": { "type": "integer", "minimum": 1, "maximum": 500, "default": 25 },
            "notifyOnAlarm": { "type": "boolean", "default": true }
          }
        }
        """));
    }

    public ValueTask<SettingsSnapshotDocument> GetSettingsAsync(CancellationToken cancellationToken)
    {
        return ValueTask.FromResult(Store.Load().ToSettingsSnapshot(Id));
    }

    public ValueTask<SettingsValidationResult> ValidateSettingsAsync(SettingsPatch patch, CancellationToken cancellationToken)
    {
        var messages = ValidatePatch(patch.Patch);
        return ValueTask.FromResult(new SettingsValidationResult(
            messages.Count == 0,
            messages,
            messages.Count == 0 ? null : new MptRuntimeError(MptErrorCodes.ValidationFailed, string.Join("; ", messages))));
    }

    public ValueTask<SettingsSnapshotDocument> ApplySettingsAsync(SettingsSnapshotDocument snapshot, CancellationToken cancellationToken)
    {
        var updated = SmartBirdSettings.Default().Apply(snapshot.Values) with { UpdatedAt = DateTimeOffset.UtcNow };
        Store.Save(updated);
        return ValueTask.FromResult(updated.ToSettingsSnapshot(Id) with { Revision = snapshot.Revision });
    }

    public ValueTask<IReadOnlyList<UiSurfaceDescriptor>> ListSurfacesAsync(CancellationToken cancellationToken)
    {
        IReadOnlyList<UiSurfaceDescriptor> surfaces =
        [
            new("smartbird-thermostat.dashboard", "dashboard-card", "SmartBird Thermostat", new JsonObject { ["moduleId"] = Id }),
            new("smartbird-thermostat.detail", "detail-page", "SmartBird Thermostat", new JsonObject { ["moduleId"] = Id }),
            new("smartbird-thermostat.settings", "settings", "SmartBird Settings", new JsonObject { ["moduleId"] = Id }),
            new("smartbird-thermostat.logs", "logs", "SmartBird Logs", new JsonObject { ["moduleId"] = Id })
        ];
        return ValueTask.FromResult(surfaces);
    }

    public ValueTask DisposeAsync(CancellationToken cancellationToken)
    {
        return ValueTask.CompletedTask;
    }

    private async Task<CommandExecutionResult> StatusSummaryAsync(CommandRequest request, CancellationToken cancellationToken)
    {
        var options = ResolveOptions(request.Args);
        var payload = await BuildStatusPayloadAsync(options, cancellationToken);
        return Succeeded(request, payload.ToJsonString());
    }

    private async Task<CommandExecutionResult> EventsListAsync(CommandRequest request, CancellationToken cancellationToken)
    {
        var options = ResolveOptions(request.Args);
        var response = await GetServiceJsonAsync(options.BaseUrl, options.EventsPath, "events", cancellationToken);
        var (events, totalEvents, truncated) = LimitEvents(response.Json, options.EventLimit);
        var payload = new JsonObject
        {
            ["moduleId"] = Id,
            ["source"] = response.ToJson(),
            ["events"] = events,
            ["totalEvents"] = totalEvents,
            ["eventLimit"] = options.EventLimit,
            ["truncated"] = truncated,
            ["state"] = response.Ok ? "ready" : "degraded",
            ["nextAction"] = response.Ok ? "" : "Expose SmartBird /api/events or keep the module in degraded event-source mode."
        };
        return Succeeded(request, payload.ToJsonString());
    }

    private CommandExecutionResult LogsSummary(CommandRequest request)
    {
        var options = ResolveOptions(request.Args);
        var directory = new DirectoryInfo(Context.LogDirectory);
        var files = directory.Exists ? directory.GetFiles("*.log") : [];
        var serviceLogPath = ResolveServiceLogPath(options.ServiceLogPath);
        var serviceLog = new FileInfo(serviceLogPath);
        var payload = new JsonObject
        {
            ["moduleId"] = Id,
            ["logDirectory"] = RedactPath(Context.LogDirectory),
            ["fileCount"] = files.Length,
            ["files"] = new JsonArray(files.Select(file => new JsonObject
            {
                ["name"] = file.Name,
                ["length"] = file.Length,
                ["updatedAt"] = file.LastWriteTimeUtc
            }).ToArray<JsonNode?>()),
            ["sourceServiceLog"] = new JsonObject
            {
                ["path"] = RedactPath(serviceLogPath),
                ["exists"] = serviceLog.Exists,
                ["length"] = serviceLog.Exists ? serviceLog.Length : 0,
                ["updatedAt"] = serviceLog.Exists ? serviceLog.LastWriteTimeUtc : null,
                ["tail"] = serviceLog.Exists ? ReadLogTail(serviceLogPath, 40) : ""
            },
            ["state"] = serviceLog.Exists || files.Length > 0 ? "ready" : "degraded",
            ["nextAction"] = serviceLog.Exists || files.Length > 0
                ? ""
                : $"Start the {options.ScheduledTaskName} scheduled task to create the original service log."
        };
        return Succeeded(request, payload.ToJsonString());
    }

    private CommandExecutionResult ConfigGet(CommandRequest request)
    {
        var options = ResolveOptions(request.Args);
        var payload = new JsonObject
        {
            ["moduleId"] = Id,
            ["localConfig"] = options.ToJson(),
            ["sourceRuntime"] = new JsonObject
            {
                ["dashboard"] = BuildUri(options.BaseUrl, "/").ToString(),
                ["status"] = BuildUri(options.BaseUrl, options.StatusPath).ToString(),
                ["events"] = BuildUri(options.BaseUrl, options.EventsPath).ToString(),
                ["energyStatus"] = BuildUri(options.BaseUrl, options.EnergyStatusPath).ToString(),
                ["energyServer"] = options.EnergyServerBaseUrl,
                ["scheduledTaskName"] = options.ScheduledTaskName,
                ["serviceLogPath"] = RedactPath(ResolveServiceLogPath(options.ServiceLogPath))
            },
            ["state"] = "ready",
            ["nextAction"] = "Use the embedded dashboard for live thermostat operations."
        };
        return Succeeded(request, payload.ToJsonString());
    }

    private CommandExecutionResult ConfigSave(CommandRequest request)
    {
        var messages = ValidatePatch(request.Args);
        if (messages.Count > 0)
        {
            return Failed(request, MptErrorCodes.ValidationFailed, string.Join("; ", messages));
        }

        var current = Store.Load();
        var updated = current.Apply(request.Args) with { UpdatedAt = DateTimeOffset.UtcNow };
        Store.Save(updated);
        File.AppendAllText(Path.Combine(Context.LogDirectory, "smartbird-thermostat.log"), $"{DateTimeOffset.UtcNow:O} config.save targetTemperatureC={updated.TargetTemperatureC}{Environment.NewLine}");
        return Succeeded(request, new JsonObject
        {
            ["moduleId"] = Id,
            ["saved"] = true,
            ["config"] = updated.ToJson()
        }.ToJsonString());
    }

    private async Task<CommandExecutionResult> HardwareDiagnosticsAsync(CommandRequest request, CancellationToken cancellationToken)
    {
        var options = ResolveOptions(request.Args);
        var checks = await CheckDependenciesAsync(options, cancellationToken);
        var payload = new JsonObject
        {
            ["moduleId"] = Id,
            ["state"] = checks.Where(check => IsRequired(check.Id)).All(check => check.Ok) ? "ready" : "degraded",
            ["dependencies"] = ToChecksJson(checks),
            ["nextAction"] = "Use the embedded dashboard to inspect Energy Server device state and SmartBird control events."
        };
        return Succeeded(request, payload.ToJsonString());
    }

    private CommandExecutionResult SelfTest(CommandRequest request)
    {
        var options = ResolveOptions(request.Args);
        var payload = new JsonObject
        {
            ["moduleId"] = Id,
            ["settingsSchema"] = "available",
            ["dataDirectory"] = RedactPath(Context.DataDirectory),
            ["cacheDirectory"] = RedactPath(Context.CacheDirectory),
            ["logDirectory"] = RedactPath(Context.LogDirectory),
            ["redaction"] = MptLogRedactor.Redact("token=abc123 secret=hidden password=hunter2 authorization=Bearer sample"),
            ["endpoints"] = new JsonObject
            {
                ["dashboard"] = BuildUri(options.BaseUrl, "/").ToString(),
                ["status"] = BuildUri(options.BaseUrl, options.StatusPath).ToString(),
                ["events"] = BuildUri(options.BaseUrl, options.EventsPath).ToString(),
                ["energyStatus"] = BuildUri(options.BaseUrl, options.EnergyStatusPath).ToString(),
                ["energyServer"] = options.EnergyServerBaseUrl
            },
            ["scheduledTaskName"] = options.ScheduledTaskName,
            ["serviceLogPath"] = RedactPath(ResolveServiceLogPath(options.ServiceLogPath)),
            ["hardware"] = new JsonObject
            {
                ["adbPath"] = options.AdbPath
            }
        };

        File.AppendAllText(Path.Combine(Context.LogDirectory, "smartbird-thermostat.log"), $"{DateTimeOffset.UtcNow:O} self-test completed{Environment.NewLine}");
        return Succeeded(request, payload.ToJsonString());
    }

    private async Task<CommandExecutionResult> RestartScheduledTaskAsync(
        CommandRequest request,
        CancellationToken cancellationToken)
    {
        if (!OperatingSystem.IsWindows())
        {
            return Failed(request, MptErrorCodes.RuntimeUnavailable, "SmartBird scheduled-task restart requires Windows.");
        }

        if (!IsInstalledUserDataDirectory(Context.DataDirectory))
        {
            return Failed(
                request,
                MptErrorCodes.RuntimeUnavailable,
                "SmartBird scheduled-task restart is available from the installed MyPowerTools user layout.");
        }

        var options = ResolveOptions(request.Args);
        var windowsDirectory = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        var schtasksPath = Path.Combine(windowsDirectory, "System32", "schtasks.exe");
        if (!File.Exists(schtasksPath))
        {
            return Failed(request, MptErrorCodes.RuntimeUnavailable, "Windows Task Scheduler command was not found.");
        }

        try
        {
            var query = await RunScheduledTaskCommandAsync(
                schtasksPath,
                ["/Query", "/TN", options.ScheduledTaskName],
                cancellationToken).ConfigureAwait(false);
            if (query.ExitCode != 0)
            {
                return Failed(
                    request,
                    MptErrorCodes.NotFound,
                    $"SmartBird scheduled task '{options.ScheduledTaskName}' is not installed: {Trim(RedactSensitive(query.Output))}");
            }

            var ended = await RunScheduledTaskCommandAsync(
                schtasksPath,
                ["/End", "/TN", options.ScheduledTaskName],
                cancellationToken).ConfigureAwait(false);
            var started = await RunScheduledTaskCommandAsync(
                schtasksPath,
                ["/Run", "/TN", options.ScheduledTaskName],
                cancellationToken).ConfigureAwait(false);
            if (started.ExitCode != 0)
            {
                return Failed(
                    request,
                    MptErrorCodes.RuntimeUnavailable,
                    $"SmartBird scheduled task failed to start: {Trim(RedactSensitive(started.Output))}",
                    retryable: true);
            }

            var reason = ReadString(request.Args, "reason") ??
                         "Restart SmartBird thermostat service after degraded diagnostics.";
            File.AppendAllText(
                Path.Combine(Context.LogDirectory, "smartbird-thermostat.log"),
                $"{DateTimeOffset.UtcNow:O} scheduled task restarted; reason={RedactSensitive(reason)}{Environment.NewLine}");
            var payload = new JsonObject
            {
                ["moduleId"] = Id,
                ["scheduledTask"] = options.ScheduledTaskName,
                ["operation"] = "end-and-run",
                ["uacRequired"] = false,
                ["endExitCode"] = ended.ExitCode,
                ["runExitCode"] = started.ExitCode,
                ["state"] = "restarted"
            };
            return Succeeded(request, payload.ToJsonString());
        }
        catch (OperationCanceledException)
        {
            return Failed(request, MptErrorCodes.CommandCancelled, "SmartBird scheduled-task restart was cancelled.");
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or Win32Exception)
        {
            return Failed(
                request,
                MptErrorCodes.RuntimeUnavailable,
                $"SmartBird scheduled-task restart failed: {ex.GetType().Name}",
                retryable: true);
        }
    }

    private static bool IsInstalledUserDataDirectory(string dataDirectory)
    {
        var dataRoot = Path.GetFullPath(Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "MyPowerTools"));
        var fullPath = Path.GetFullPath(dataDirectory);
        var prefix = dataRoot.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) +
                     Path.DirectorySeparatorChar;
        return fullPath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase);
    }

    private static async Task<ScheduledTaskCommandResult> RunScheduledTaskCommandAsync(
        string schtasksPath,
        IReadOnlyList<string> arguments,
        CancellationToken cancellationToken)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = schtasksPath,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        foreach (var argument in arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = Process.Start(startInfo)
            ?? throw new IOException("Windows Task Scheduler command did not start.");
        var outputTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var errorTask = process.StandardError.ReadToEndAsync(cancellationToken);
        await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
        var output = string.Join(Environment.NewLine, await outputTask.ConfigureAwait(false), await errorTask.ConfigureAwait(false));
        return new ScheduledTaskCommandResult(process.ExitCode, output);
    }

    private sealed record ScheduledTaskCommandResult(int ExitCode, string Output);

    private async Task<JsonObject> BuildStatusPayloadAsync(SmartBirdSettings options, CancellationToken cancellationToken)
    {
        var service = await ProbeHttpAsync(options.BaseUrl, options.StatusPath, "smartbird.status", "SmartBird HTTP status", cancellationToken);
        var dependencies = await CheckDependenciesAsync(options, cancellationToken);
        var checks = new[] { service }.Concat(dependencies).ToArray();
        var requiredOk = checks.Where(check => IsRequired(check.Id)).All(check => check.Ok);
        return new JsonObject
        {
            ["moduleId"] = Id,
            ["state"] = requiredOk ? "running" : "degraded",
            ["summary"] = requiredOk ? "SmartBird service and required dependencies are reachable." : SummarizeDegraded(checks),
            ["targetTemperatureC"] = options.TargetTemperatureC,
            ["pollIntervalSeconds"] = options.PollIntervalSeconds,
            ["notifyOnAlarm"] = options.NotifyOnAlarm,
            ["checks"] = ToChecksJson(checks),
            ["service"] = service.ToJson(),
            ["dependencies"] = ToChecksJson(dependencies),
            ["nextAction"] = requiredOk ? "" : "Connect SmartBird hardware services or update settings with reachable endpoints."
        };
    }

    private async Task<IReadOnlyList<SmartBirdCheck>> CheckDependenciesAsync(SmartBirdSettings options, CancellationToken cancellationToken)
    {
        var energy = await CheckEnergyServerAsync(options, cancellationToken);
        var adb = await CheckAdbAsync(options, cancellationToken);
        return [energy, adb];
    }

    private async Task<SmartBirdCheck> CheckEnergyServerAsync(
        SmartBirdSettings options,
        CancellationToken cancellationToken)
    {
        var response = await GetServiceJsonAsync(
            options.BaseUrl,
            options.EnergyStatusPath,
            "energy-status",
            cancellationToken);
        var status = response.Json as JsonObject;
        var online = response.Ok && ReadBool(status ?? new JsonObject(), "online") == true;
        var configuredUrl = ReadString(status ?? new JsonObject(), "url") ?? options.EnergyServerBaseUrl;
        var error = ReadString(status ?? new JsonObject(), "error");
        var message = online
            ? $"Energy Server online at {configuredUrl}."
            : !string.IsNullOrWhiteSpace(error)
                ? error
                : response.Message;
        return new SmartBirdCheck(
            "energy-server.status",
            "Energy Server",
            online,
            message,
            response.Uri,
            true);
    }

    private async Task<SmartBirdCheck> ProbeHttpAsync(string baseUrl, string path, string id, string label, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(baseUrl))
        {
            return new SmartBirdCheck(id, label, false, "Endpoint is not configured.", null, true);
        }

        try
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromMilliseconds(1200));
            var uri = BuildUri(baseUrl, path);
            using var response = await _httpClient.GetAsync(uri, timeout.Token);
            var body = RedactSensitive(await response.Content.ReadAsStringAsync(timeout.Token));
            var message = response.IsSuccessStatusCode
                ? $"HTTP {(int)response.StatusCode}: source endpoint reachable."
                : $"HTTP {(int)response.StatusCode}: {Trim(body)}";
            return new SmartBirdCheck(id, label, response.IsSuccessStatusCode, message, uri.ToString(), true);
        }
        catch (OperationCanceledException)
        {
            return new SmartBirdCheck(id, label, false, $"Timed out while checking {MptLogRedactor.Redact(baseUrl)}.", BuildUri(baseUrl, path).ToString(), true);
        }
        catch (Exception ex)
        {
            return new SmartBirdCheck(id, label, false, MptLogRedactor.Redact(ex.Message), SafeUri(baseUrl, path), true);
        }
    }

    private static async Task<SmartBirdCheck> CheckAdbAsync(SmartBirdSettings options, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(options.AdbPath))
        {
            return new SmartBirdCheck("adb.devices", "ADB devices", false, "ADB path is not configured.", null, true);
        }

        var result = await RunToolAsync(options.AdbPath, ["devices"], TimeSpan.FromSeconds(3), cancellationToken);
        return new SmartBirdCheck("adb.devices", "ADB devices", result.Available && result.ExitCode == 0, result.Message, options.AdbPath, true);
    }

    private async Task<ServiceJsonResult> GetServiceJsonAsync(string baseUrl, string path, string source, CancellationToken cancellationToken)
    {
        try
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromMilliseconds(1200));
            var uri = BuildUri(baseUrl, path);
            using var response = await _httpClient.GetAsync(uri, timeout.Token);
            var text = RedactSensitive(await response.Content.ReadAsStringAsync(timeout.Token));
            JsonNode? json = null;
            try
            {
                json = string.IsNullOrWhiteSpace(text) ? null : JsonNode.Parse(text);
            }
            catch (JsonException)
            {
                json = new JsonObject { ["text"] = Trim(text) };
            }

            return new ServiceJsonResult(source, response.IsSuccessStatusCode, (int)response.StatusCode, uri.ToString(), Trim(text), json);
        }
        catch (OperationCanceledException)
        {
            return new ServiceJsonResult(source, false, 0, SafeUri(baseUrl, path), "Timed out while querying SmartBird service.", null);
        }
        catch (Exception ex)
        {
            return new ServiceJsonResult(source, false, 0, SafeUri(baseUrl, path), MptLogRedactor.Redact(ex.Message), null);
        }
    }

    private SmartBirdSettings ResolveOptions(JsonObject args)
    {
        return Store.Load().Apply(args);
    }

    private static IReadOnlyList<string> ValidatePatch(JsonObject patch)
    {
        var messages = new List<string>();
        ValidateCanonicalString(patch, "baseUrl", CanonicalBaseUrl, messages);
        ValidateCanonicalString(patch, "scheduledTaskName", CanonicalScheduledTaskName, messages);

        foreach (var key in new[] { "energyServerBaseUrl" })
        {
            if (patch.TryGetPropertyValue(key, out var node) && node is not null)
            {
                try
                {
                    if (!TryGetLoopbackHttpUri(node.GetValue<string>(), out _))
                    {
                        messages.Add($"{key} must be a loopback HTTP URL without credentials.");
                    }
                }
                catch (InvalidOperationException)
                {
                    messages.Add($"{key} must be a string.");
                }
            }
        }

        foreach (var key in new[] { "statusPath", "eventsPath", "energyStatusPath" })
        {
            if (patch.TryGetPropertyValue(key, out var node) && node is not null)
            {
                try
                {
                    var path = node.GetValue<string>();
                    if (!path.StartsWith("/", StringComparison.Ordinal))
                    {
                        messages.Add($"{key} must start with '/'.");
                    }
                }
                catch (InvalidOperationException)
                {
                    messages.Add($"{key} must be a string.");
                }
            }
        }

        if (ReadDouble(patch, "targetTemperatureC") is { } target && (target < 0 || target > 120))
        {
            messages.Add("targetTemperatureC must be between 0 and 120.");
        }

        if (ReadInt(patch, "pollIntervalSeconds") is { } interval && (interval < 5 || interval > 3600))
        {
            messages.Add("pollIntervalSeconds must be between 5 and 3600.");
        }

        if (ReadInt(patch, "eventLimit") is { } eventLimit && (eventLimit < 1 || eventLimit > 500))
        {
            messages.Add("eventLimit must be between 1 and 500.");
        }

        return messages;
    }

    private static void ValidateCanonicalString(
        JsonObject patch,
        string key,
        string expected,
        ICollection<string> messages)
    {
        if (!patch.TryGetPropertyValue(key, out var node))
        {
            return;
        }

        try
        {
            if (node is null ||
                !string.Equals(node.GetValue<string>(), expected, StringComparison.Ordinal))
            {
                messages.Add($"{key} is fixed and must equal '{expected}'.");
            }
        }
        catch (InvalidOperationException)
        {
            messages.Add($"{key} is fixed and must equal '{expected}'.");
        }
    }

    private static (JsonArray Events, int TotalEvents, bool Truncated) LimitEvents(JsonNode? json, int limit)
    {
        var source = json switch
        {
            JsonArray array => array,
            JsonObject obj when obj["events"] is JsonArray events => events,
            _ => new JsonArray()
        };
        var total = source.Count;
        var start = Math.Max(0, total - limit);
        var limited = new JsonArray();
        for (var i = start; i < total; i++)
        {
            limited.Add(source[i]?.DeepClone());
        }

        return (limited, total, total > limited.Count);
    }

    private static IReadOnlyList<HealthCheckSnapshot> ReadChecks(JsonObject payload)
    {
        var checks = new List<HealthCheckSnapshot>();
        if (payload["checks"] is not JsonArray array)
        {
            return checks;
        }

        foreach (var item in array.OfType<JsonObject>())
        {
            checks.Add(new HealthCheckSnapshot(
                item["id"]?.GetValue<string>() ?? "unknown",
                item["label"]?.GetValue<string>() ?? "Unknown",
                item["ok"]?.GetValue<bool>() ?? false,
                item["message"]?.GetValue<string>() ?? ""));
        }

        return checks;
    }

    private static JsonArray ToChecksJson(IEnumerable<SmartBirdCheck> checks)
    {
        return new JsonArray(checks.Select(check => check.ToJson()).ToArray<JsonNode?>());
    }

    private static bool IsRequired(string id)
    {
        return id is "smartbird.status" or "energy-server.status" or "adb.devices";
    }

    private static Uri BuildUri(string baseUrl, string path)
    {
        return new Uri(new Uri(baseUrl.TrimEnd('/') + "/"), path.TrimStart('/'));
    }

    private static bool TryGetLoopbackHttpUri(string? value, out Uri uri)
    {
        if (!string.IsNullOrWhiteSpace(value) &&
            Uri.TryCreate(value.Trim(), UriKind.Absolute, out var parsed) &&
            parsed.IsLoopback &&
            string.Equals(parsed.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase) &&
            string.IsNullOrEmpty(parsed.UserInfo))
        {
            uri = parsed;
            return true;
        }

        uri = null!;
        return false;
    }

    private static string NormalizeLoopbackHttpUrl(string value, string fallback)
    {
        return TryGetLoopbackHttpUri(value, out var uri)
            ? uri.AbsoluteUri.TrimEnd('/')
            : fallback;
    }

    private static string SafeUri(string baseUrl, string path)
    {
        try
        {
            return BuildUri(baseUrl, path).ToString();
        }
        catch
        {
            return MptLogRedactor.Redact($"{baseUrl}{path}");
        }
    }

    private static MptCommandDescriptor Command(
        string id,
        string title,
        string subtitle,
        bool requiresElevation = false,
        string dangerLevel = "",
        IReadOnlyList<CommandParameterDescriptor>? parameters = null)
    {
        var execution = new JsonObject { ["type"] = "module.execute" };
        IReadOnlyList<string>? constraints = null;
        if (requiresElevation)
        {
            execution["brokerApprovalOnly"] = true;
            constraints =
            [
                MptOperationConstraints.MutatesSystemState,
                MptOperationConstraints.RequiresElevatedWrites
            ];
        }

        return new MptCommandDescriptor(
            id,
            "smartbird-thermostat",
            title,
            subtitle,
            "action",
            requiresElevation,
            DangerLevel: dangerLevel,
            Category: "SmartBird Thermostat",
            TimeoutMs: 10000,
            Execution: execution,
            Parameters: parameters,
            Constraints: constraints);
    }

    private static IReadOnlyList<CommandParameterDescriptor> FacadeParameters()
    {
        return
        [
            new CommandParameterDescriptor("baseUrl", "Base URL", "text", false, ""),
            new CommandParameterDescriptor("statusPath", "Status path", "text", false, "/api/status"),
            new CommandParameterDescriptor("eventsPath", "Events path", "text", false, "/api/events"),
            new CommandParameterDescriptor("energyServerBaseUrl", "Energy server URL", "text", false, ""),
            new CommandParameterDescriptor("energyStatusPath", "Energy status path", "text", false, "/api/energy/status")
        ];
    }

    private static IReadOnlyList<CommandParameterDescriptor> ConfigSaveParameters()
    {
        return
        [
            new CommandParameterDescriptor("baseUrl", "Base URL", "text", false, ""),
            new CommandParameterDescriptor("statusPath", "Status path", "text", false, "/api/status"),
            new CommandParameterDescriptor("targetTemperatureC", "Target temperature C", "number", false, "22"),
            new CommandParameterDescriptor("pollIntervalSeconds", "Poll interval seconds", "number", false, "30")
        ];
    }

    private static IReadOnlyList<CommandParameterDescriptor> RestartParameters()
    {
        return
        [
            new CommandParameterDescriptor("reason", "Reason", "multiline", false, "Restart SmartBird thermostat service after degraded diagnostics.")
        ];
    }

    private static CommandExecutionResult Succeeded(CommandRequest request, string output)
    {
        return new CommandExecutionResult(request.InvocationId, request.CommandId, "succeeded", true, output);
    }

    private static CommandExecutionResult Failed(CommandRequest request, string code, string message, bool retryable = false, JsonObject? details = null)
    {
        return new CommandExecutionResult(request.InvocationId, request.CommandId, "failed", false, "", new MptRuntimeError(code, message, retryable, details));
    }

    private static string SummarizeDegraded(IEnumerable<SmartBirdCheck> checks)
    {
        var failed = checks.Where(check => !check.Ok && check.Required).Select(check => $"{check.Label}: {check.Message}").ToArray();
        return failed.Length == 0 ? "SmartBird dependencies are reachable." : string.Join("; ", failed);
    }

    private static string SummarizeDegraded(IEnumerable<HealthCheckSnapshot> checks)
    {
        var failed = checks.Where(check => !check.Ok && IsRequired(check.Id)).Select(check => $"{check.Label}: {check.Message}").ToArray();
        return failed.Length == 0 ? "SmartBird dependencies are reachable." : string.Join("; ", failed);
    }

    private static string Trim(string value)
    {
        value = value.ReplaceLineEndings(" ").Trim();
        return value.Length <= 600 ? value : value[..600] + "...";
    }

    private static string RedactPath(string path)
    {
        return RedactSensitive(path);
    }

    private static string RedactSensitive(string value)
    {
        value = MptLogRedactor.Redact(value);
        value = AdbSerialPropertyPattern.Replace(value, "\"adb_serial\":\"<adb-device-list>\"");
        value = DeviceEndpointPattern.Replace(value, match =>
        {
            return IPAddress.TryParse(match.Groups["host"].Value, out var address) && IPAddress.IsLoopback(address)
                ? match.Value
                : "<device-endpoint>";
        });
        var replacements = new[]
        {
            (Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "%LOCALAPPDATA%"),
            (Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "%USERPROFILE%")
        };

        foreach (var (path, token) in replacements.Where(item => !string.IsNullOrWhiteSpace(item.Item1)).OrderByDescending(item => item.Item1.Length))
        {
            value = value.Replace(path, token, StringComparison.OrdinalIgnoreCase);
            value = value.Replace(path.Replace("\\", "\\\\", StringComparison.Ordinal), token, StringComparison.OrdinalIgnoreCase);
        }

        return value;
    }

    private string ResolveServiceLogPath(string configuredPath)
    {
        if (!string.IsNullOrWhiteSpace(configuredPath))
        {
            return Path.GetFullPath(Environment.ExpandEnvironmentVariables(configuredPath));
        }

        var userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var candidates = new[]
        {
            Path.Combine(userProfile, "repo", "androidtools", "logs", "smartbird_thermostat_service.log"),
            Path.GetFullPath(Path.Combine(Environment.CurrentDirectory, "..", "androidtools", "logs", "smartbird_thermostat_service.log")),
            Path.Combine(Context.LogDirectory, "smartbird_thermostat_service.log")
        };
        return candidates.FirstOrDefault(File.Exists) ?? candidates[0];
    }

    private static string ReadLogTail(string path, int lineCount)
    {
        try
        {
            using var stream = new FileStream(
                path,
                FileMode.Open,
                FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete);
            var start = Math.Max(0, stream.Length - (256 * 1024));
            stream.Seek(start, SeekOrigin.Begin);
            using var reader = new StreamReader(stream);
            if (start > 0)
            {
                _ = reader.ReadLine();
            }

            var tail = new Queue<string>(Math.Max(1, lineCount));
            while (reader.ReadLine() is { } line)
            {
                if (tail.Count == lineCount)
                {
                    tail.Dequeue();
                }
                tail.Enqueue(line);
            }
            return RedactSensitive(string.Join(Environment.NewLine, tail));
        }
        catch (Exception ex)
        {
            return $"Unable to read service log: {MptLogRedactor.Redact(ex.Message)}";
        }
    }

    private static string? ReadString(JsonObject args, string key)
    {
        if (!args.TryGetPropertyValue(key, out var node) || node is null)
        {
            return null;
        }

        try
        {
            return node.GetValue<string>();
        }
        catch (InvalidOperationException)
        {
            return null;
        }
    }

    private static int? ReadInt(JsonObject args, string key)
    {
        if (!args.TryGetPropertyValue(key, out var node) || node is null)
        {
            return null;
        }

        try
        {
            return node.GetValue<int>();
        }
        catch (InvalidOperationException)
        {
            try
            {
                return checked((int)node.GetValue<long>());
            }
            catch
            {
                return null;
            }
        }
    }

    private static double? ReadDouble(JsonObject args, string key)
    {
        if (!args.TryGetPropertyValue(key, out var node) || node is null)
        {
            return null;
        }

        try
        {
            return node.GetValue<double>();
        }
        catch (InvalidOperationException)
        {
            try
            {
                return node.GetValue<int>();
            }
            catch (InvalidOperationException)
            {
                try
                {
                    return node.GetValue<long>();
                }
                catch (InvalidOperationException)
                {
                    return null;
                }
            }
        }
    }

    private static bool? ReadBool(JsonObject args, string key)
    {
        if (!args.TryGetPropertyValue(key, out var node) || node is null)
        {
            return null;
        }

        try
        {
            return node.GetValue<bool>();
        }
        catch (InvalidOperationException)
        {
            return null;
        }
    }

    private static async Task<ToolResult> RunToolAsync(string fileName, IReadOnlyList<string> arguments, TimeSpan timeout, CancellationToken cancellationToken)
    {
        var startedAt = DateTimeOffset.UtcNow;
        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeoutCts.CancelAfter(timeout);

        var psi = new ProcessStartInfo
        {
            FileName = fileName,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        };

        foreach (var argument in arguments)
        {
            psi.ArgumentList.Add(argument);
        }

        try
        {
            using var process = Process.Start(psi);
            if (process is null)
            {
                return ToolResult.Failed(fileName, -1, "Process could not be started.");
            }

            var stdoutTask = process.StandardOutput.ReadToEndAsync(timeoutCts.Token);
            var stderrTask = process.StandardError.ReadToEndAsync(timeoutCts.Token);
            await process.WaitForExitAsync(timeoutCts.Token);
            var stdout = RedactAdbOutput(await stdoutTask);
            var stderr = MptLogRedactor.Redact(await stderrTask);
            return new ToolResult(fileName, true, process.ExitCode, Trim(stdout), Trim(stderr), DateTimeOffset.UtcNow - startedAt);
        }
        catch (Win32Exception ex) when (ex.NativeErrorCode == 2)
        {
            return ToolResult.Missing(fileName, $"{fileName} executable was not found on PATH.");
        }
        catch (OperationCanceledException)
        {
            return ToolResult.Failed(fileName, -1, $"{fileName} timed out after {timeout.TotalSeconds:n0}s.");
        }
        catch (Exception ex)
        {
            return ToolResult.Failed(fileName, -1, MptLogRedactor.Redact(ex.Message));
        }
    }

    private static string RedactAdbOutput(string value)
    {
        value = MptLogRedactor.Redact(value);
        var lines = value.Replace("\r\n", "\n").Split('\n');
        var deviceIndex = 1;
        for (var i = 0; i < lines.Length; i++)
        {
            var line = lines[i];
            if (string.IsNullOrWhiteSpace(line) || line.StartsWith("List of devices", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            var firstWhitespace = line.AsSpan().IndexOfAny(' ', '\t');
            if (firstWhitespace <= 0)
            {
                continue;
            }

            lines[i] = $"<adb-device-{deviceIndex++}>{line[firstWhitespace..]}";
        }

        return string.Join(Environment.NewLine, lines);
    }

    private sealed record ServiceJsonResult(string Source, bool Ok, int StatusCode, string Uri, string Message, JsonNode? Json)
    {
        public JsonObject ToJson()
        {
            return new JsonObject
            {
                ["source"] = Source,
                ["ok"] = Ok,
                ["statusCode"] = StatusCode,
                ["uri"] = Uri,
                ["message"] = Message
            };
        }
    }

    private sealed record SmartBirdCheck(string Id, string Label, bool Ok, string Message, string? Uri, bool Required)
    {
        public JsonObject ToJson()
        {
            return new JsonObject
            {
                ["id"] = Id,
                ["label"] = Label,
                ["ok"] = Ok,
                ["message"] = Message,
                ["uri"] = Uri,
                ["required"] = Required
            };
        }
    }

    private sealed record ToolResult(string Tool, bool Available, int ExitCode, string Stdout, string Stderr, TimeSpan Duration)
    {
        public string Message
        {
            get
            {
                if (!Available)
                {
                    return Stderr;
                }

                if (ExitCode == 0)
                {
                    return string.IsNullOrWhiteSpace(Stdout) ? "Command completed." : Stdout;
                }

                return string.IsNullOrWhiteSpace(Stderr) ? $"Exited with code {ExitCode}." : Stderr;
            }
        }

        public static ToolResult Missing(string tool, string message)
        {
            return new ToolResult(tool, false, -1, "", message, TimeSpan.Zero);
        }

        public static ToolResult Failed(string tool, int exitCode, string message)
        {
            return new ToolResult(tool, true, exitCode, "", message, TimeSpan.Zero);
        }
    }

    private sealed class SmartBirdStore
    {
        private readonly string _path;

        public SmartBirdStore(string path)
        {
            _path = path;
        }

        public void EnsureDefaults()
        {
            if (!File.Exists(_path))
            {
                Save(SmartBirdSettings.Default());
            }
        }

        public SmartBirdSettings Load()
        {
            try
            {
                if (!File.Exists(_path))
                {
                    return SmartBirdSettings.Default();
                }

                return (JsonSerializer.Deserialize<SmartBirdSettings>(File.ReadAllText(_path), JsonOptions) ?? SmartBirdSettings.Default()).Normalize();
            }
            catch
            {
                return SmartBirdSettings.Default();
            }
        }

        public void Save(SmartBirdSettings settings)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_path)!);
            var tmp = _path + ".tmp";
            File.WriteAllText(tmp, JsonSerializer.Serialize(settings, JsonOptions));
            File.Move(tmp, _path, overwrite: true);
        }
    }

    private sealed record SmartBirdSettings(
        string BaseUrl,
        string StatusPath,
        string EventsPath,
        string ServiceLogPath,
        string ScheduledTaskName,
        string EnergyServerBaseUrl,
        string EnergyStatusPath,
        string AdbPath,
        double TargetTemperatureC,
        int PollIntervalSeconds,
        int EventLimit,
        bool NotifyOnAlarm,
        DateTimeOffset UpdatedAt)
    {
        public static SmartBirdSettings Default()
        {
            return new SmartBirdSettings(
                CanonicalBaseUrl,
                "/api/status",
                "/api/events",
                "",
                CanonicalScheduledTaskName,
                "http://127.0.0.1:18988",
                "/api/energy/status",
                "adb",
                45,
                30,
                25,
                true,
                DateTimeOffset.UtcNow);
        }

        public SmartBirdSettings Apply(JsonObject patch)
        {
            return (this with
            {
                BaseUrl = CanonicalBaseUrl,
                StatusPath = ReadString(patch, "statusPath") ?? StatusPath,
                EventsPath = ReadString(patch, "eventsPath") ?? EventsPath,
                ServiceLogPath = ReadString(patch, "serviceLogPath") ?? ServiceLogPath,
                ScheduledTaskName = CanonicalScheduledTaskName,
                EnergyServerBaseUrl = ReadString(patch, "energyServerBaseUrl") ?? EnergyServerBaseUrl,
                EnergyStatusPath = ReadString(patch, "energyStatusPath") ?? EnergyStatusPath,
                AdbPath = ReadString(patch, "adbPath") ?? AdbPath,
                TargetTemperatureC = ReadDouble(patch, "targetTemperatureC") ?? TargetTemperatureC,
                PollIntervalSeconds = ReadInt(patch, "pollIntervalSeconds") ?? PollIntervalSeconds,
                EventLimit = ReadInt(patch, "eventLimit") ?? EventLimit,
                NotifyOnAlarm = ReadBool(patch, "notifyOnAlarm") ?? NotifyOnAlarm
            }).Normalize();
        }

        public SmartBirdSettings Normalize()
        {
            var defaults = Default();
            return this with
            {
                BaseUrl = CanonicalBaseUrl,
                StatusPath = NormalizePath(StatusPath, defaults.StatusPath),
                EventsPath = NormalizePath(EventsPath, defaults.EventsPath),
                ServiceLogPath = ServiceLogPath ?? "",
                ScheduledTaskName = CanonicalScheduledTaskName,
                EnergyServerBaseUrl = NormalizeLoopbackHttpUrl(EnergyServerBaseUrl, defaults.EnergyServerBaseUrl),
                EnergyStatusPath = NormalizePath(EnergyStatusPath, defaults.EnergyStatusPath),
                AdbPath = string.IsNullOrWhiteSpace(AdbPath) ? defaults.AdbPath : AdbPath,
                TargetTemperatureC = TargetTemperatureC is < 0 or > 120 ? defaults.TargetTemperatureC : TargetTemperatureC,
                PollIntervalSeconds = PollIntervalSeconds is < 5 or > 3600 ? defaults.PollIntervalSeconds : PollIntervalSeconds,
                EventLimit = EventLimit is < 1 or > 500 ? defaults.EventLimit : EventLimit
            };
        }

        public SettingsSnapshotDocument ToSettingsSnapshot(string moduleId)
        {
            return new SettingsSnapshotDocument(moduleId, 1, ToJson(), UpdatedAt);
        }

        public JsonObject ToJson()
        {
            return new JsonObject
            {
                ["baseUrl"] = BaseUrl,
                ["statusPath"] = StatusPath,
                ["eventsPath"] = EventsPath,
                ["serviceLogPath"] = ServiceLogPath,
                ["scheduledTaskName"] = ScheduledTaskName,
                ["energyServerBaseUrl"] = EnergyServerBaseUrl,
                ["energyStatusPath"] = EnergyStatusPath,
                ["adbPath"] = AdbPath,
                ["targetTemperatureC"] = TargetTemperatureC,
                ["pollIntervalSeconds"] = PollIntervalSeconds,
                ["eventLimit"] = EventLimit,
                ["notifyOnAlarm"] = NotifyOnAlarm,
                ["updatedAt"] = UpdatedAt
            };
        }

        private static string NormalizePath(string value, string fallback)
        {
            return string.IsNullOrWhiteSpace(value) || !value.StartsWith("/", StringComparison.Ordinal) ? fallback : value;
        }
    }
}
