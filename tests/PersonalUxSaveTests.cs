using Avalonia.Headless.XUnit;
using SmartBird.Surface.Services;
using SmartBird.Surface.ViewModels;
using Xunit;

namespace PersonalUx.Tests;

public sealed class PersonalUxSaveTests
{
    [AvaloniaFact]
    public async Task Save_only_writes_on_the_first_click_without_requiring_or_restarting_the_runtime()
    {
        var root = Path.Combine(Path.GetTempPath(), "mpt-smartbird-save-" + Guid.NewGuid().ToString("N"));
        try
        {
            var service = new SmartBirdThermostatSettingsService(root);
            var vm = new SmartBirdThermostatViewModel(
                SmartBirdThermostatSnapshot.Offline(new Uri("http://127.0.0.1:19002"), "offline", "not installed"),
                settingsService: service);
            vm.AdbSerials = "my-phone";
            vm.LoopSec = "15";
            vm.SaveSettingsOnlyCommand.Execute(null);
            var deadline = DateTime.UtcNow.AddSeconds(5);
            while (vm.IsBusy && DateTime.UtcNow < deadline) await Task.Delay(10);
            Assert.False(vm.IsBusy);
            Assert.True(File.Exists(service.SettingsPath), vm.SettingsStatus);
            var state = await service.LoadAsync();
            Assert.Equal("my-phone", state.Settings.AdbSerials);
            Assert.Equal(15, state.Settings.LoopSec);
            Assert.Contains("服务未重启", vm.SettingsStatus);
        }
        finally { if (Directory.Exists(root)) Directory.Delete(root, true); }
    }

    [Fact]
    public async Task Invalid_settings_leave_the_existing_saved_configuration_unchanged()
    {
        var root = Path.Combine(Path.GetTempPath(), "mpt-smartbird-save-" + Guid.NewGuid().ToString("N"));
        try
        {
            var service = new SmartBirdThermostatSettingsService(root);
            await service.SaveAsync(new SmartBirdThermostatSettings(), null, null);
            var before = await File.ReadAllTextAsync(service.SettingsPath);
            await Assert.ThrowsAnyAsync<Exception>(() => service.SaveAsync(new SmartBirdThermostatSettings { ServicePort = -1 }, null, null));
            Assert.Equal(before, await File.ReadAllTextAsync(service.SettingsPath));
        }
        finally { if (Directory.Exists(root)) Directory.Delete(root, true); }
    }
}
