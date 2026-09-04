using MyPowerTools.AvaloniaSdk;

namespace SmartBird.Surface.ViewModels;

public sealed partial class SmartBirdThermostatViewModel
{
    private bool _singleClickSettingsSaveEnabled;

    public void EnableSingleClickSettingsSave()
    {
        if (_singleClickSettingsSaveEnabled)
        {
            return;
        }

        _singleClickSettingsSaveEnabled = true;
        SaveSettingsCommand = new MptAsyncRelayCommand(
            SaveSettingsImmediatelyAsync,
            () => !IsBusy);
        OnPropertyChanged(nameof(SaveSettingsCommand));
    }

    private Task SaveSettingsImmediatelyAsync()
    {
        _settingsConfirmed = true;
        return SaveSettingsAsync();
    }
}
