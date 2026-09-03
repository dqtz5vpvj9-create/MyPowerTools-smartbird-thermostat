using System.Globalization;
using System.Windows.Input;
using SmartBird.Surface.Services;

using MyPowerTools.AvaloniaSdk;
namespace SmartBird.Surface.ViewModels;

public sealed partial class SmartBirdThermostatViewModel
{
    private readonly SmartBirdThermostatSettingsService _settingsService;
    private bool _isSettingsVisible;
    private string _settingsStatus = "设置尚未读取。";
    private string _settingsPath = "";
    private string _serviceHost = "127.0.0.1";
    private string _servicePort = "19002";
    private string _smartBirdHost = "0.0.0.0";
    private string _smartBirdPort = "19001";
    private string _adbSerials = "";
    private string _loopSec = "10";
    private string _minOnSec = "60";
    private string _minOffSec = "60";
    private string _marginC = "5";
    private string _condensationGuardC = "3";
    private string _minSurfaceC = "30";
    private string _onSurfaceC = "35";
    private string _hysteresisC = "4";
    private string _defaultAmbientC = "28";
    private string _defaultRh = "95";
    private string _amapCity = "310112";
    private string _amapTimeoutSec = "3";
    private string _weatherRefreshSec = "300";
    private string _amapKeyInput = "";
    private bool _settingsConfirmed;
    private bool _removeStoredAmapKey;
    private bool _hasStoredAmapKey;
    private bool _energyServerEnabled;
    private string _energyServerUrl = "http://127.0.0.1:18988";
    private string _energyBackend = "hid";
    private string _usbMeterSelectorMode = "auto";
    private string _usbMeterSelector = "";
    private bool _energyAllowUnsafeControl;
    private bool _notificationsEnabled;
    private string _smtpHost = "smtp.163.com";
    private string _smtpPort = "465";
    private bool _smtpSsl = true;
    private bool _smtpStartTls;
    private string _smtpUsername = "";
    private string _smtpSender = "";
    private string _smtpRecipients = "";
    private string _smtpPasswordInput = "";
    private bool _removeStoredSmtpPassword;
    private bool _hasStoredSmtpPassword;
    private string _notificationMonitorSec = "30";
    private string _notificationCooldownSec = "1800";
    private bool _notificationSendRecovery = true;
    private string _notificationExpectedMinDevices = "0";

    public IReadOnlyList<string> EnergyBackendOptions { get; } = ["hid", "uia"];
    public IReadOnlyList<string> UsbMeterSelectorModeOptions { get; } = ["auto", "serial", "path", "title"];

    public ICommand ShowConsoleCommand { get; private set; } = null!;
    public ICommand ShowSettingsCommand { get; private set; } = null!;
    public ICommand SaveSettingsCommand { get; private set; } = null!;
    public ICommand TestConnectionsCommand { get; private set; } = null!;
    public ICommand StartEnergyServerCommand { get; private set; } = null!;
    public ICommand StopEnergyServerCommand { get; private set; } = null!;

    public bool IsSettingsVisible
    {
        get => _isSettingsVisible;
        private set
        {
            if (SetProperty(ref _isSettingsVisible, value))
            {
                OnPropertyChanged(nameof(IsConsoleVisible));
            }
        }
    }
    public bool IsConsoleVisible => !IsSettingsVisible;
    public string SettingsStatus { get => _settingsStatus; private set => SetProperty(ref _settingsStatus, value); }
    public string SettingsPath { get => _settingsPath; private set => SetProperty(ref _settingsPath, value); }
    public string ServiceHost { get => _serviceHost; set => SetProperty(ref _serviceHost, value); }
    public string ServicePort { get => _servicePort; set => SetProperty(ref _servicePort, value); }
    public string SmartBirdHost { get => _smartBirdHost; set => SetProperty(ref _smartBirdHost, value); }
    public string SmartBirdPort { get => _smartBirdPort; set => SetProperty(ref _smartBirdPort, value); }
    public string AdbSerials { get => _adbSerials; set => SetProperty(ref _adbSerials, value); }
    public string LoopSec { get => _loopSec; set => SetProperty(ref _loopSec, value); }
    public string MinOnSec { get => _minOnSec; set => SetProperty(ref _minOnSec, value); }
    public string MinOffSec { get => _minOffSec; set => SetProperty(ref _minOffSec, value); }
    public string MarginC { get => _marginC; set => SetProperty(ref _marginC, value); }
    public string CondensationGuardC { get => _condensationGuardC; set => SetProperty(ref _condensationGuardC, value); }
    public string MinSurfaceC { get => _minSurfaceC; set => SetProperty(ref _minSurfaceC, value); }
    public string OnSurfaceC { get => _onSurfaceC; set => SetProperty(ref _onSurfaceC, value); }
    public string HysteresisC { get => _hysteresisC; set => SetProperty(ref _hysteresisC, value); }
    public string DefaultAmbientC { get => _defaultAmbientC; set => SetProperty(ref _defaultAmbientC, value); }
    public string DefaultRh { get => _defaultRh; set => SetProperty(ref _defaultRh, value); }
    public string AmapCity { get => _amapCity; set => SetProperty(ref _amapCity, value); }
    public string AmapTimeoutSec { get => _amapTimeoutSec; set => SetProperty(ref _amapTimeoutSec, value); }
    public string WeatherRefreshSec { get => _weatherRefreshSec; set => SetProperty(ref _weatherRefreshSec, value); }
    public string AmapKeyInput { get => _amapKeyInput; set => SetProperty(ref _amapKeyInput, value); }
    public bool RemoveStoredAmapKey { get => _removeStoredAmapKey; set => SetProperty(ref _removeStoredAmapKey, value); }
    public bool HasStoredAmapKey { get => _hasStoredAmapKey; private set => SetProperty(ref _hasStoredAmapKey, value); }
    public string AmapKeyState => HasStoredAmapKey ? "已有密钥；留空将继续使用" : "尚未保存密钥";
    public bool EnergyServerEnabled { get => _energyServerEnabled; set => SetProperty(ref _energyServerEnabled, value); }
    public string EnergyServerUrl { get => _energyServerUrl; set => SetProperty(ref _energyServerUrl, value); }
    public string EnergyBackend { get => _energyBackend; set => SetProperty(ref _energyBackend, value); }
    public string UsbMeterSelectorMode { get => _usbMeterSelectorMode; set => SetProperty(ref _usbMeterSelectorMode, value); }
    public string UsbMeterSelector { get => _usbMeterSelector; set => SetProperty(ref _usbMeterSelector, value); }
    public bool EnergyAllowUnsafeControl { get => _energyAllowUnsafeControl; set => SetProperty(ref _energyAllowUnsafeControl, value); }
    public bool NotificationsEnabled { get => _notificationsEnabled; set => SetProperty(ref _notificationsEnabled, value); }
    public string SmtpHost { get => _smtpHost; set => SetProperty(ref _smtpHost, value); }
    public string SmtpPort { get => _smtpPort; set => SetProperty(ref _smtpPort, value); }
    public bool SmtpSsl { get => _smtpSsl; set => SetProperty(ref _smtpSsl, value); }
    public bool SmtpStartTls { get => _smtpStartTls; set => SetProperty(ref _smtpStartTls, value); }
    public string SmtpUsername { get => _smtpUsername; set => SetProperty(ref _smtpUsername, value); }
    public string SmtpSender { get => _smtpSender; set => SetProperty(ref _smtpSender, value); }
    public string SmtpRecipients { get => _smtpRecipients; set => SetProperty(ref _smtpRecipients, value); }
    public string SmtpPasswordInput { get => _smtpPasswordInput; set => SetProperty(ref _smtpPasswordInput, value); }
    public bool RemoveStoredSmtpPassword { get => _removeStoredSmtpPassword; set => SetProperty(ref _removeStoredSmtpPassword, value); }
    public bool HasStoredSmtpPassword { get => _hasStoredSmtpPassword; private set => SetProperty(ref _hasStoredSmtpPassword, value); }
    public string SmtpPasswordState => HasStoredSmtpPassword ? "已有密码；留空将继续使用" : "尚未保存密码";
    public string NotificationMonitorSec { get => _notificationMonitorSec; set => SetProperty(ref _notificationMonitorSec, value); }
    public string NotificationCooldownSec { get => _notificationCooldownSec; set => SetProperty(ref _notificationCooldownSec, value); }
    public bool NotificationSendRecovery { get => _notificationSendRecovery; set => SetProperty(ref _notificationSendRecovery, value); }
    public string NotificationExpectedMinDevices { get => _notificationExpectedMinDevices; set => SetProperty(ref _notificationExpectedMinDevices, value); }

    private void InitializeSettingsCommands()
    {
        ShowConsoleCommand = new MptAsyncRelayCommand(() =>
        {
            IsSettingsVisible = false;
            return Task.CompletedTask;
        });
        ShowSettingsCommand = new MptAsyncRelayCommand(() =>
        {
            IsSettingsVisible = true;
            _settingsConfirmed = false;
            return Task.CompletedTask;
        });
        SaveSettingsCommand = new MptAsyncRelayCommand(SaveSettingsAsync, () => !IsBusy);
        TestConnectionsCommand = new MptAsyncRelayCommand(TestConnectionsAsync, () => !IsBusy);
        StartEnergyServerCommand = new MptAsyncRelayCommand(StartEnergyServerAsync, () => !IsBusy);
        StopEnergyServerCommand = new MptAsyncRelayCommand(StopEnergyServerAsync, () => !IsBusy);
    }

    public async Task InitializeSettingsAsync()
    {
        try
        {
            var state = await _settingsService.LoadAsync().ConfigureAwait(true);
            ApplySettingsState(state);
            SettingsStatus = "已读取当前用户的 SmartBird 配置。";
        }
        catch (Exception ex)
        {
            SettingsStatus = $"读取设置失败：{ex.Message}";
        }
    }

    private async Task SaveSettingsAsync()
    {
        if (!_settingsConfirmed)
        {
            _settingsConfirmed = true;
            SettingsStatus = "确认保存并重启服务？再次点击“保存”以确认。";
            return;
        }

        _settingsConfirmed = false;
        IsBusy = true;
        try
        {
            SettingsStatus = "正在保存并重启后台任务…";
            var result = await _settingsService.SaveAndApplyAsync(
                BuildSettings(),
                RemoveStoredAmapKey ? "" : EmptyToNull(AmapKeyInput),
                RemoveStoredSmtpPassword ? "" : EmptyToNull(SmtpPasswordInput)).ConfigureAwait(true);
            ApplySettingsState(result.State);
            AmapKeyInput = "";
            SmtpPasswordInput = "";
            RemoveStoredAmapKey = false;
            RemoveStoredSmtpPassword = false;
            SettingsStatus = result.Message;
            if (_refresh is not null)
            {
                ApplySnapshot(await _refresh().ConfigureAwait(true));
            }
        }
        catch (Exception ex)
        {
            SettingsStatus = $"应用失败：{ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    private async Task TestConnectionsAsync()
    {
        IsBusy = true;
        try
        {
            SettingsStatus = "正在测试 SmartBird、ADB、Energy Server 与通知连接…";
            SettingsStatus = await _settingsService.TestConnectionsAsync(
                BuildSettings(),
                EmptyToNull(AmapKeyInput),
                EmptyToNull(SmtpPasswordInput)).ConfigureAwait(true);
        }
        catch (Exception ex)
        {
            SettingsStatus = $"连接测试失败：{ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    private async Task StartEnergyServerAsync()
    {
        IsBusy = true;
        try { SettingsStatus = await _settingsService.StartEnergyServerAsync().ConfigureAwait(true); }
        catch (Exception ex) { SettingsStatus = $"Energy Server 启动失败：{ex.Message}"; }
        finally { IsBusy = false; }
    }

    private async Task StopEnergyServerAsync()
    {
        IsBusy = true;
        try { SettingsStatus = await _settingsService.StopEnergyServerAsync().ConfigureAwait(true); }
        catch (Exception ex) { SettingsStatus = $"Energy Server 停止失败：{ex.Message}"; }
        finally { IsBusy = false; }
    }

    private SmartBirdThermostatSettings BuildSettings()
    {
        return new SmartBirdThermostatSettings
        {
            ServiceHost = ServiceHost.Trim(),
            ServicePort = ParseInt(ServicePort, "Web 服务端口"),
            SmartBirdHost = SmartBirdHost.Trim(),
            SmartBirdPort = ParseInt(SmartBirdPort, "SmartBird TCP 端口"),
            AdbSerials = AdbSerials.Trim(),
            LoopSec = ParseDouble(LoopSec, "循环周期"),
            MinOnSec = ParseDouble(MinOnSec, "最短开启时间"),
            MinOffSec = ParseDouble(MinOffSec, "最短关闭时间"),
            MarginC = ParseDouble(MarginC, "露点余量"),
            CondensationGuardC = ParseDouble(CondensationGuardC, "冷凝保护差值"),
            MinSurfaceC = ParseDouble(MinSurfaceC, "最低表面温度"),
            OnSurfaceC = ParseDouble(OnSurfaceC, "开启表面温度"),
            HysteresisC = ParseDouble(HysteresisC, "温控滞回"),
            DefaultAmbientC = ParseDouble(DefaultAmbientC, "备用温度"),
            DefaultRh = ParseDouble(DefaultRh, "备用湿度"),
            AmapCity = AmapCity.Trim(),
            AmapTimeoutSec = ParseDouble(AmapTimeoutSec, "高德超时"),
            WeatherRefreshSec = ParseDouble(WeatherRefreshSec, "天气刷新周期"),
            EnergyServerEnabled = EnergyServerEnabled,
            EnergyServerUrl = EnergyServerUrl.Trim(),
            EnergyBackend = EnergyBackend,
            UsbMeterSelectorMode = UsbMeterSelectorMode,
            UsbMeterSelector = UsbMeterSelector.Trim(),
            EnergyAllowUnsafeControl = EnergyAllowUnsafeControl,
            NotificationsEnabled = NotificationsEnabled,
            SmtpHost = SmtpHost.Trim(),
            SmtpPort = ParseInt(SmtpPort, "SMTP 端口"),
            SmtpSsl = SmtpSsl,
            SmtpStartTls = SmtpStartTls,
            SmtpUsername = SmtpUsername.Trim(),
            SmtpSender = SmtpSender.Trim(),
            SmtpRecipients = SmtpRecipients.Trim(),
            NotificationMonitorSec = ParseDouble(NotificationMonitorSec, "通知检查周期"),
            NotificationCooldownSec = ParseDouble(NotificationCooldownSec, "通知冷却时间"),
            NotificationSendRecovery = NotificationSendRecovery,
            NotificationExpectedMinDevices = ParseInt(NotificationExpectedMinDevices, "期望设备数")
        };
    }

    private void ApplySettingsState(SmartBirdSettingsState state)
    {
        var value = state.Settings;
        ServiceHost = value.ServiceHost;
        ServicePort = Number(value.ServicePort);
        SmartBirdHost = value.SmartBirdHost;
        SmartBirdPort = Number(value.SmartBirdPort);
        AdbSerials = value.AdbSerials;
        LoopSec = Number(value.LoopSec);
        MinOnSec = Number(value.MinOnSec);
        MinOffSec = Number(value.MinOffSec);
        MarginC = Number(value.MarginC);
        CondensationGuardC = Number(value.CondensationGuardC);
        MinSurfaceC = Number(value.MinSurfaceC);
        OnSurfaceC = Number(value.OnSurfaceC);
        HysteresisC = Number(value.HysteresisC);
        DefaultAmbientC = Number(value.DefaultAmbientC);
        DefaultRh = Number(value.DefaultRh);
        AmapCity = value.AmapCity;
        AmapTimeoutSec = Number(value.AmapTimeoutSec);
        WeatherRefreshSec = Number(value.WeatherRefreshSec);
        EnergyServerEnabled = value.EnergyServerEnabled;
        EnergyServerUrl = value.EnergyServerUrl;
        EnergyBackend = value.EnergyBackend;
        UsbMeterSelectorMode = value.UsbMeterSelectorMode;
        UsbMeterSelector = value.UsbMeterSelector;
        EnergyAllowUnsafeControl = value.EnergyAllowUnsafeControl;
        NotificationsEnabled = value.NotificationsEnabled;
        SmtpHost = value.SmtpHost;
        SmtpPort = Number(value.SmtpPort);
        SmtpSsl = value.SmtpSsl;
        SmtpStartTls = value.SmtpStartTls;
        SmtpUsername = value.SmtpUsername;
        SmtpSender = value.SmtpSender;
        SmtpRecipients = value.SmtpRecipients;
        NotificationMonitorSec = Number(value.NotificationMonitorSec);
        NotificationCooldownSec = Number(value.NotificationCooldownSec);
        NotificationSendRecovery = value.NotificationSendRecovery;
        NotificationExpectedMinDevices = Number(value.NotificationExpectedMinDevices);
        SettingsPath = state.SettingsPath;
        HasStoredAmapKey = state.HasAmapKey;
        HasStoredSmtpPassword = state.HasSmtpPassword;
        OnPropertyChanged(nameof(AmapKeyState));
        OnPropertyChanged(nameof(SmtpPasswordState));
    }

    private void NotifySettingsCommandStateChanged()
    {
        if (SaveSettingsCommand is MptAsyncRelayCommand save) save.NotifyCanExecuteChanged();
        if (TestConnectionsCommand is MptAsyncRelayCommand test) test.NotifyCanExecuteChanged();
        if (StartEnergyServerCommand is MptAsyncRelayCommand start) start.NotifyCanExecuteChanged();
        if (StopEnergyServerCommand is MptAsyncRelayCommand stop) stop.NotifyCanExecuteChanged();
    }

    private static int ParseInt(string value, string label) =>
        int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed)
            ? parsed
            : throw new InvalidOperationException($"{label}必须是整数。");

    private static double ParseDouble(string value, string label) =>
        double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var parsed)
            ? parsed
            : throw new InvalidOperationException($"{label}必须是数字。");

    private static string Number<T>(T value) where T : IFormattable =>
        value.ToString(null, CultureInfo.InvariantCulture);

    private static string? EmptyToNull(string value) => string.IsNullOrEmpty(value) ? null : value;
}
