using MyPowerTools.AvaloniaSdk;
using SmartBird.Surface.ViewModels;

namespace SmartBird.Surface.Views;

public partial class SmartBirdThermostatView : IMptShortcutCommandSource
{
    public string ShortcutToolId => "smartbird-thermostat";
    public string ShortcutContext => DataContext is SmartBirdThermostatViewModel vm ? vm.IsSettingsVisible ? "settings" : "console" : "";

    public IReadOnlyList<MptShortcutCommand> GetShortcutCommands()
    {
        if (DataContext is not SmartBirdThermostatViewModel vm) return [];
        return
        [
            MptShortcutCommand.FromCommand("smartbird-thermostat.ui.refresh", vm.RefreshCommand),
            MptShortcutCommand.FromCommand("smartbird-thermostat.ui.start-service", vm.StartServiceCommand),
            MptShortcutCommand.FromCommand("smartbird-thermostat.ui.open-in-browser", vm.OpenInBrowserCommand),
            MptShortcutCommand.FromCommand("smartbird-thermostat.ui.show-console", vm.ShowConsoleCommand),
            MptShortcutCommand.FromCommand("smartbird-thermostat.ui.show-settings", vm.ShowSettingsCommand),
            MptShortcutCommand.FromCommand("smartbird-thermostat.ui.save-settings-only", vm.SaveSettingsOnlyCommand),
            MptShortcutCommand.FromCommand("smartbird-thermostat.ui.save-settings", vm.SaveSettingsCommand),
            MptShortcutCommand.FromCommand("smartbird-thermostat.ui.test-connections", vm.TestConnectionsCommand),
            MptShortcutCommand.FromCommand("smartbird-thermostat.ui.start-energy-server", vm.StartEnergyServerCommand),
            MptShortcutCommand.FromCommand("smartbird-thermostat.ui.stop-energy-server", vm.StopEnergyServerCommand),
        ];
    }
}
