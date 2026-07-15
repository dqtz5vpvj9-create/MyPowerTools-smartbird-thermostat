using System.ComponentModel;
using Avalonia.Controls;
using SmartBird.Surface.ViewModels;

namespace SmartBird.Surface.Views;

public partial class SmartBirdThermostatView : UserControl
{
    private SmartBirdThermostatViewModel? _viewModel;

    public SmartBirdThermostatView()
    {
        InitializeComponent();
        EmbeddedBrowser.StateChanged += HandleBrowserStateChanged;
        DataContextChanged += HandleDataContextChanged;
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
        }
    }

    private void HandleViewModelPropertyChanged(object? sender, PropertyChangedEventArgs eventArgs)
    {
        if (eventArgs.PropertyName == nameof(SmartBirdThermostatViewModel.ReloadVersion))
        {
            EmbeddedBrowser.Reload();
        }
    }

    private void HandleBrowserStateChanged(object? sender, SmartBirdWebViewStateChangedEventArgs eventArgs)
    {
        if (_viewModel is null)
        {
            return;
        }

        switch (eventArgs.State)
        {
            case SmartBirdWebViewState.Loading:
                _viewModel.SetEmbeddedBrowserLoading();
                break;
            case SmartBirdWebViewState.Ready:
                _viewModel.SetEmbeddedBrowserReady();
                break;
            case SmartBirdWebViewState.Unavailable:
                _viewModel.SetEmbeddedBrowserUnavailable(eventArgs.Message);
                break;
            case SmartBirdWebViewState.Failed:
                _viewModel.SetEmbeddedBrowserFailed(eventArgs.Message);
                break;
        }
    }
}
