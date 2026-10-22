using SmartBird.Surface.Services;
using Xunit;

namespace PersonalUx.Tests;

public sealed class SmartBirdNumericSettingsTests
{
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

    private sealed class TemporaryData : IDisposable
    {
        public string Root { get; } = Path.Combine(Path.GetTempPath(), "mpt-smartbird-e2e-" + Guid.NewGuid().ToString("N"));
        public void Dispose() { if (Directory.Exists(Root)) Directory.Delete(Root, true); }
    }
}
