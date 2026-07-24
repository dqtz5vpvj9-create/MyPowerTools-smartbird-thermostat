using System.ComponentModel;
using Avalonia.Controls;
using MyPowerTools.AvaloniaSdk;
using SmartBird.Surface.ViewModels;

namespace SmartBird.Surface.Views;

public partial class SmartBirdThermostatView : UserControl, IDisposable
{
    private readonly SmartBirdWebSurfaceSessionController _webSurface;
    private SmartBirdThermostatViewModel? _viewModel;
    private int _disposed;

    public SmartBirdThermostatView()
        : this(null, "smartbird-thermostat", "overview")
    {
    }

    public SmartBirdThermostatView(
        IMptWebSurfaceService? webSurfaces,
        string toolId,
        string routeId)
    {
        InitializeComponent();
        _webSurface = new SmartBirdWebSurfaceSessionController(
            webSurfaces,
            toolId,
            routeId,
            control => EmbeddedBrowserHost.Content = control,
            ApplyBrowserState);
        DataContextChanged += HandleDataContextChanged;
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0)
        {
            return;
        }
        DataContextChanged -= HandleDataContextChanged;
        if (_viewModel is not null)
        {
            _viewModel.PropertyChanged -= HandleViewModelPropertyChanged;
            _viewModel = null;
        }
        _webSurface.Dispose();
    }

    private void HandleDataContextChanged(object? sender, EventArgs eventArgs)
    {
        if (_viewModel is not null)
        {
            _viewModel.PropertyChanged -= HandleViewModelPropertyChanged;
        }

        _viewModel = DataContext as SmartBirdThermostatViewModel;
        if (_viewModel is not null)
        {
            _viewModel.PropertyChanged += HandleViewModelPropertyChanged;
            _webSurface.SetSource(_viewModel.DashboardUri);
        }
        else
        {
            _webSurface.Clear();
        }
    }

    private void HandleViewModelPropertyChanged(object? sender, PropertyChangedEventArgs eventArgs)
    {
        if (eventArgs.PropertyName == nameof(SmartBirdThermostatViewModel.DashboardUri))
        {
            if (_viewModel is not null)
            {
                _webSurface.SetSource(_viewModel.DashboardUri);
            }
        }
        else if (eventArgs.PropertyName == nameof(SmartBirdThermostatViewModel.ReloadVersion))
        {
            if (_viewModel is not null)
            {
                _webSurface.Reload(_viewModel.DashboardUri);
            }
        }
    }

    private void ApplyBrowserState(MptWebSurfaceState state, string message)
    {
        if (_viewModel is null)
        {
            return;
        }
        switch (state)
        {
            case MptWebSurfaceState.Loading:
                _viewModel.SetEmbeddedBrowserLoading();
                break;
            case MptWebSurfaceState.Ready:
                _viewModel.SetEmbeddedBrowserReady();
                break;
            case MptWebSurfaceState.Unavailable:
                _viewModel.SetEmbeddedBrowserUnavailable(message);
                break;
            case MptWebSurfaceState.Failed:
                _viewModel.SetEmbeddedBrowserFailed(message);
                break;
        }
    }
}
