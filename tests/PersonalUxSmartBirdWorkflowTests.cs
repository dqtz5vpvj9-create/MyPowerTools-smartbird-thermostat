using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json.Nodes;
using Avalonia.Headless.XUnit;
using MyPowerTools.AvaloniaSdk;
using SmartBird.Surface.Services;
using SmartBird.Surface.ViewModels;
using Xunit;

namespace PersonalUx.Tests;

public sealed class SmartBirdWorkflowTests
{
    [Fact]
    public async Task Saved_endpoint_refreshes_online_then_invalid_then_unavailable_over_real_http()
    {
        using var data = new TemporaryData();
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        var settings = new SmartBirdThermostatSettingsService(data.Root);
        await settings.SaveAsync(new() { ServicePort = port, AdbSerials = "" }, null, null);
        using var service = new SmartBirdThermostatToolService(settingsService: settings);
        var serve = ServeAsync(listener, [
            (200, "{\"mode\":\"dewpoint_protection\",\"last_key\":1,\"last_decision\":{\"surface_c\":36.5},\"switch\":{\"client_count\":2}}"),
            (200, "[]"),
            (503, "{}")]);
        var online = await service.LoadAsync();
        Assert.True(online.IsOnline);
        Assert.Equal(port, online.DashboardUri.Port);
        Assert.True(online.CoolingEnabled);
        Assert.Equal(36.5, online.SurfaceC);
        Assert.Equal(2, online.ClientCount);
        var invalid = await service.LoadAsync();
        Assert.False(invalid.IsOnline);
        Assert.Equal("invalid-status", invalid.ErrorCode);
        Assert.Equal(port, invalid.DashboardUri.Port);
        var unavailable = await service.LoadAsync();
        Assert.False(unavailable.IsOnline);
        Assert.Equal("ServiceUnavailable", unavailable.ErrorCode);
        Assert.Equal(port, unavailable.DashboardUri.Port);
        await serve.WaitAsync(TimeSpan.FromSeconds(10));
    }

    [Fact]
    public async Task Connection_test_contacts_only_the_fake_tcp_device_with_empty_adb_list()
    {
        using var data = new TemporaryData();
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var accept = listener.AcceptTcpClientAsync();
        var service = new SmartBirdThermostatSettingsService(data.Root);
        var message = await service.TestConnectionsAsync(new()
        {
            SmartBirdHost = "127.0.0.1", SmartBirdPort = ((IPEndPoint)listener.LocalEndpoint).Port,
            AdbSerials = "", EnergyServerEnabled = false, NotificationsEnabled = false
        }, "", "");
        using var client = await accept.WaitAsync(TimeSpan.FromSeconds(5));
        Assert.Contains("SmartBird TCP：可达", message);
        Assert.Contains("ADB：未配置设备", message);
        Assert.Contains("高德天气：未配置密钥", message);
        Assert.False(File.Exists(service.SettingsPath));
    }

    [Fact]
    public async Task Saved_notification_recipients_roundtrip_without_embedding_passwords()
    {
        using var data = new TemporaryData();
        var service = new SmartBirdThermostatSettingsService(data.Root);
        await service.SaveAsync(new()
        {
            AdbSerials = "fake-device", NotificationsEnabled = true,
            SmtpRecipients = " a@example.test; b@example.test\r\nc@example.test ",
            NotificationMonitorSec = 12, NotificationCooldownSec = 0
        }, null, null);
        var reloaded = await new SmartBirdThermostatSettingsService(data.Root).LoadAsync();
        Assert.Equal("fake-device", reloaded.Settings.AdbSerials);
        Assert.Equal(12, reloaded.Settings.NotificationMonitorSec);
        var notification = JsonNode.Parse(await File.ReadAllTextAsync(service.NotificationConfigPath))!;
        Assert.True(notification["enabled"]!.GetValue<bool>());
        Assert.Equal("", notification["password"]!.GetValue<string>());
        Assert.Equal(new[] { "a@example.test", "b@example.test", "c@example.test" },
            notification["recipients"]!.AsArray().Select(x => x!.GetValue<string>()).ToArray());
    }

    [Theory]
    [InlineData("weather", -1)]
    [InlineData("weather", 0)]
    [InlineData("amap", 0)]
    [InlineData("monitor", 0)]
    [InlineData("monitor", -1)]
    [InlineData("cooldown", -1)]
    [InlineData("devices", -1)]
    [InlineData("loop", double.NaN)]
    [InlineData("temperature", double.PositiveInfinity)]
    public async Task Invalid_numeric_settings_preserve_both_existing_files(string field, double value)
    {
        using var data = new TemporaryData();
        var service = new SmartBirdThermostatSettingsService(data.Root);
        var valid = new SmartBirdThermostatSettings { AdbSerials = "" };
        await service.SaveAsync(valid, null, null);
        var before = await File.ReadAllTextAsync(service.SettingsPath);
        var notificationBefore = await File.ReadAllTextAsync(service.NotificationConfigPath);
        var invalid = field switch
        {
            "weather" => valid with { WeatherRefreshSec = value },
            "amap" => valid with { AmapTimeoutSec = value },
            "monitor" => valid with { NotificationMonitorSec = value },
            "cooldown" => valid with { NotificationCooldownSec = value },
            "devices" => valid with { NotificationExpectedMinDevices = (int)value },
            "loop" => valid with { LoopSec = value },
            _ => valid with { DefaultAmbientC = value }
        };
        await Assert.ThrowsAsync<InvalidOperationException>(() => service.SaveAsync(invalid, null, null));
        Assert.Equal(before, await File.ReadAllTextAsync(service.SettingsPath));
        Assert.Equal(notificationBefore, await File.ReadAllTextAsync(service.NotificationConfigPath));
    }

    [AvaloniaFact]
    public async Task Settings_commands_save_reload_show_validation_and_preserve_console_state()
    {
        using var data = new TemporaryData();
        var settings = new SmartBirdThermostatSettingsService(data.Root);
        var snapshot = new SmartBirdThermostatSnapshot(new("http://127.0.0.1:19002/"), true,
            "dewpoint_protection", false, 1, 31, "online", "fake", DateTimeOffset.Now, "");
        var vm = new SmartBirdThermostatViewModel(snapshot, settingsService: settings);
        await vm.InitializeSettingsAsync();
        await ((MptAsyncRelayCommand)vm.ShowSettingsCommand).ExecuteAsync();
        Assert.True(vm.IsSettingsVisible);
        vm.AdbSerials = "fake-only";
        vm.LoopSec = "15";
        await ((MptAsyncRelayCommand)vm.SaveSettingsOnlyCommand).ExecuteAsync();
        Assert.False(vm.IsBusy);
        Assert.Contains("服务未重启", vm.SettingsStatus);
        Assert.Equal(15, (await settings.LoadAsync()).Settings.LoopSec);
        vm.LoopSec = "invalid";
        await ((MptAsyncRelayCommand)vm.SaveSettingsOnlyCommand).ExecuteAsync();
        Assert.Contains("保存失败", vm.SettingsStatus);
        Assert.False(vm.IsBusy);
        Assert.Equal(15, (await settings.LoadAsync()).Settings.LoopSec);
        await ((MptAsyncRelayCommand)vm.ShowConsoleCommand).ExecuteAsync();
        Assert.True(vm.IsConsoleVisible);
        Assert.True(vm.IsServiceOnline);
        Assert.Equal("制冷关闭", vm.CoolingLabel);
    }

    private static async Task ServeAsync(TcpListener listener, (int Code, string Json)[] responses)
    {
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        foreach (var response in responses)
        {
            using var client = await listener.AcceptTcpClientAsync(deadline.Token);
            await using var stream = client.GetStream();
            using var reader = new StreamReader(stream, Encoding.ASCII, leaveOpen: true);
            Assert.Equal("GET /api/status HTTP/1.1", await reader.ReadLineAsync(deadline.Token));
            while (await reader.ReadLineAsync(deadline.Token) is { Length: > 0 }) { }
            var body = Encoding.UTF8.GetBytes(response.Json);
            var header = Encoding.ASCII.GetBytes($"HTTP/1.1 {response.Code} Test\r\nContent-Type: application/json\r\nContent-Length: {body.Length}\r\nConnection: close\r\n\r\n");
            await stream.WriteAsync(header, deadline.Token);
            await stream.WriteAsync(body, deadline.Token);
        }
    }

    private sealed class TemporaryData : IDisposable
    {
        public string Root { get; } = Path.Combine(Path.GetTempPath(), "mpt-smartbird-e2e-" + Guid.NewGuid().ToString("N"));
        public void Dispose() { if (Directory.Exists(Root)) Directory.Delete(Root, true); }
    }
}
