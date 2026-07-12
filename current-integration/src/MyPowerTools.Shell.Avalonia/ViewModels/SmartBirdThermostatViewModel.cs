using System.Diagnostics;
using System.Globalization;
using System.Windows.Input;
using MyPowerTools.Shell.Avalonia.Services;

namespace MyPowerTools.Shell.Avalonia.ViewModels;

public enum SmartBirdEmbeddedBrowserState
{
    Loading,
    Ready,
    Unavailable,
    Failed
}

public sealed partial class SmartBirdThermostatViewModel : ToolProductPageViewModel
{
    private readonly Func<Task<SmartBirdThermostatSnapshot>>? _refresh;
    private readonly Func<Task<SmartBirdThermostatSnapshot>>? _startService;
    private readonly Func<Uri, Task> _openExternal;
    private SmartBirdThermostatSnapshot _snapshot;
    private SmartBirdEmbeddedBrowserState _browserState;
    private string _browserError = "";
    private bool _isBusy;
    private int _reloadVersion;

    public SmartBirdThermostatViewModel(
        SmartBirdThermostatSnapshot snapshot,
        Func<Task<SmartBirdThermostatSnapshot>>? refresh = null,
        Func<Task<SmartBirdThermostatSnapshot>>? startService = null,
        Func<Uri, Task>? openExternal = null,
        bool embeddedBrowserSupported = true,
        SmartBirdThermostatSettingsService? settingsService = null)
        : base(
            "SmartBird 温度管理器",
            "露点保护、散热器控制与能耗会话",
            ToolProductState.Ready)
    {
        _snapshot = snapshot;
        _refresh = refresh;
        _startService = startService;
        _openExternal = openExternal ?? OpenWithSystemBrowserAsync;
        _settingsService = settingsService ?? new SmartBirdThermostatSettingsService();
        EmbeddedBrowserSupported = embeddedBrowserSupported;
        _browserState = embeddedBrowserSupported
            ? SmartBirdEmbeddedBrowserState.Loading
            : SmartBirdEmbeddedBrowserState.Unavailable;

        RefreshCommand = new AsyncRelayCommand(RefreshAsync, () => !IsBusy);
        StartServiceCommand = new AsyncRelayCommand(StartServiceAsync, () => CanStartService);
        OpenInBrowserCommand = new AsyncRelayCommand(
            () => OpenExternalAsync(DashboardUri),
            () => !IsBusy);
        InitializeSettingsCommands();
    }

    public bool EmbeddedBrowserSupported { get; }
    public ICommand RefreshCommand { get; }
    public ICommand StartServiceCommand { get; }
    public ICommand OpenInBrowserCommand { get; }
    public Uri DashboardUri => _snapshot.DashboardUri;
    public string DashboardAddress => DashboardUri.ToString();
    public bool IsServiceOnline => _snapshot.IsOnline;
    public bool IsServiceOffline => !IsServiceOnline;
    public bool IsBrowserLoading => IsServiceOnline && _browserState == SmartBirdEmbeddedBrowserState.Loading;
    public bool IsBrowserReady => IsServiceOnline && _browserState == SmartBirdEmbeddedBrowserState.Ready;
    public bool IsBrowserUnavailable => _browserState == SmartBirdEmbeddedBrowserState.Unavailable;
    public bool IsBrowserFailed => _browserState == SmartBirdEmbeddedBrowserState.Failed;
    public bool IsWebViewHostVisible => IsServiceOnline &&
                                        EmbeddedBrowserSupported &&
                                        _browserState is not (SmartBirdEmbeddedBrowserState.Unavailable or SmartBirdEmbeddedBrowserState.Failed);
    public bool IsFallbackVisible => !IsWebViewHostVisible;
    public bool CanStartService => !IsBusy && IsServiceOffline && _startService is not null;
    public bool ShowStartServiceAction => IsServiceOffline && _startService is not null;
    public string ConnectionLabel => IsBusy
        ? "正在连接"
        : IsServiceOnline
            ? "服务已连接"
            : "服务离线";
    public string ConnectionDetail => _snapshot.StatusDetail;
    public string StatusTitle => _snapshot.StatusTitle;
    public string CheckedAtLabel => $"上次检查 {_snapshot.CheckedAt:HH:mm:ss}";
    public string ModeLabel => _snapshot.Mode switch
    {
        "dewpoint_protection" => "露点保护",
        "experiment_unconditional" => "能耗实验",
        _ => "状态待确认"
    };
    public string CoolingLabel => _snapshot.CoolingEnabled switch
    {
        true => "制冷开启",
        false => "制冷关闭",
        null => "制冷状态待确认"
    };
    public string SurfaceLabel => _snapshot.SurfaceC is null
        ? "温度待确认"
        : $"表面 {_snapshot.SurfaceC.Value.ToString("0.0", CultureInfo.InvariantCulture)} °C";
    public string DeviceLabel => _snapshot.ClientCount == 1
        ? "1 台设备在线"
        : $"{_snapshot.ClientCount} 台设备在线";
    public string BrowserError => _browserError;
    public string FallbackTitle
    {
        get
        {
            if (IsServiceOffline)
            {
                return StatusTitle;
            }
            return IsBrowserFailed
                ? "SmartBird 控制台加载失败"
                : "当前环境无法嵌入 WebView2";
        }
    }
    public string FallbackDetail
    {
        get
        {
            if (IsServiceOffline)
            {
                return ConnectionDetail;
            }
            if ((IsBrowserFailed || IsBrowserUnavailable) && !string.IsNullOrWhiteSpace(BrowserError))
            {
                return BrowserError;
            }
            return "服务已经在线。可在系统浏览器中打开完整控制台。";
        }
    }
    public string FallbackActionHint => IsServiceOffline
        ? $"后台任务：{SmartBirdThermostatToolService.ScheduledTaskName}"
        : DashboardAddress;
    public int ReloadVersion => _reloadVersion;

    public bool IsBusy
    {
        get => _isBusy;
        private set
        {
            if (!SetProperty(ref _isBusy, value))
            {
                return;
            }
            NotifyStateChanged();
            ((AsyncRelayCommand)RefreshCommand).NotifyCanExecuteChanged();
            ((AsyncRelayCommand)StartServiceCommand).NotifyCanExecuteChanged();
            ((AsyncRelayCommand)OpenInBrowserCommand).NotifyCanExecuteChanged();
            NotifySettingsCommandStateChanged();
        }
    }

    public void SetEmbeddedBrowserLoading()
    {
        SetBrowserState(SmartBirdEmbeddedBrowserState.Loading, "");
    }

    public void SetEmbeddedBrowserReady()
    {
        SetBrowserState(SmartBirdEmbeddedBrowserState.Ready, "");
    }

    public void SetEmbeddedBrowserUnavailable(string message)
    {
        SetBrowserState(SmartBirdEmbeddedBrowserState.Unavailable, message);
    }

    public void SetEmbeddedBrowserFailed(string message)
    {
        SetBrowserState(SmartBirdEmbeddedBrowserState.Failed, message);
    }

    public Task OpenExternalAsync(Uri uri)
    {
        if (!SmartBirdThermostatToolService.IsDashboardOrigin(uri))
        {
            throw new InvalidOperationException("SmartBird 控制台只能打开本机 127.0.0.1:19002。");
        }
        return _openExternal(uri);
    }

    private async Task RefreshAsync()
    {
        if (_refresh is null)
        {
            _reloadVersion++;
            OnPropertyChanged(nameof(ReloadVersion));
            return;
        }

        await RunBusyAsync(async () =>
        {
            ApplySnapshot(await _refresh().ConfigureAwait(true));
            if (IsServiceOnline && EmbeddedBrowserSupported)
            {
                SetEmbeddedBrowserLoading();
                _reloadVersion++;
                OnPropertyChanged(nameof(ReloadVersion));
            }
        }).ConfigureAwait(true);
    }

    private async Task StartServiceAsync()
    {
        if (_startService is null)
        {
            return;
        }

        await RunBusyAsync(async () =>
        {
            ApplySnapshot(await _startService().ConfigureAwait(true));
            if (IsServiceOnline && EmbeddedBrowserSupported)
            {
                SetEmbeddedBrowserLoading();
                _reloadVersion++;
                OnPropertyChanged(nameof(ReloadVersion));
            }
        }).ConfigureAwait(true);
    }

    private async Task RunBusyAsync(Func<Task> action)
    {
        IsBusy = true;
        try
        {
            await action().ConfigureAwait(true);
        }
        catch (Exception ex)
        {
            _snapshot = SmartBirdThermostatSnapshot.Offline(
                DashboardUri,
                "SmartBird 操作失败。",
                string.IsNullOrWhiteSpace(ex.Message) ? "请稍后重试。" : ex.Message,
                "operation-failed");
            NotifyStateChanged();
        }
        finally
        {
            IsBusy = false;
        }
    }

    private void ApplySnapshot(SmartBirdThermostatSnapshot snapshot)
    {
        _snapshot = snapshot;
        if (!snapshot.IsOnline && EmbeddedBrowserSupported)
        {
            _browserState = SmartBirdEmbeddedBrowserState.Loading;
            _browserError = "";
        }
        NotifyStateChanged();
    }

    private void SetBrowserState(SmartBirdEmbeddedBrowserState state, string message)
    {
        _browserState = state;
        _browserError = message;
        NotifyStateChanged();
    }

    private void NotifyStateChanged()
    {
        OnPropertyChanged(nameof(DashboardUri));
        OnPropertyChanged(nameof(DashboardAddress));
        OnPropertyChanged(nameof(IsServiceOnline));
        OnPropertyChanged(nameof(IsServiceOffline));
        OnPropertyChanged(nameof(IsBrowserLoading));
        OnPropertyChanged(nameof(IsBrowserReady));
        OnPropertyChanged(nameof(IsBrowserUnavailable));
        OnPropertyChanged(nameof(IsBrowserFailed));
        OnPropertyChanged(nameof(IsWebViewHostVisible));
        OnPropertyChanged(nameof(IsFallbackVisible));
        OnPropertyChanged(nameof(CanStartService));
        OnPropertyChanged(nameof(ShowStartServiceAction));
        OnPropertyChanged(nameof(ConnectionLabel));
        OnPropertyChanged(nameof(ConnectionDetail));
        OnPropertyChanged(nameof(StatusTitle));
        OnPropertyChanged(nameof(CheckedAtLabel));
        OnPropertyChanged(nameof(ModeLabel));
        OnPropertyChanged(nameof(CoolingLabel));
        OnPropertyChanged(nameof(SurfaceLabel));
        OnPropertyChanged(nameof(DeviceLabel));
        OnPropertyChanged(nameof(BrowserError));
        OnPropertyChanged(nameof(FallbackTitle));
        OnPropertyChanged(nameof(FallbackDetail));
        OnPropertyChanged(nameof(FallbackActionHint));
        ((AsyncRelayCommand)StartServiceCommand).NotifyCanExecuteChanged();
    }

    private static Task OpenWithSystemBrowserAsync(Uri uri)
    {
        var startInfo = new ProcessStartInfo(uri.AbsoluteUri)
        {
            UseShellExecute = true
        };
        Process.Start(startInfo);
        return Task.CompletedTask;
    }
}
