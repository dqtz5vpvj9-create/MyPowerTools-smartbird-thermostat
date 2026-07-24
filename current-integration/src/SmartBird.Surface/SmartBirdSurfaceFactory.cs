using Avalonia.Controls;
using MyPowerTools.AvaloniaSdk;
using SmartBird.Surface.Services;
using SmartBird.Surface.ViewModels;
using SmartBird.Surface.Views;

namespace SmartBird.Surface;

/// <summary>
/// Dotnet-surface factory for the SmartBird thermostat tool. Loaded by the Shell's
/// DotnetSurfaceLoader from this assembly via the route's <c>assembly</c>+<c>type</c> manifest
/// fields. Builds the SmartBirdThermostatViewModel from the shared
/// <see cref="SmartBirdThermostatToolService"/> snapshot, mirroring the Shell controller's load
/// path (including the settings initialization step) but operating independently through
/// <see cref="MptAvaloniaSurfaceContext"/>.
/// </summary>
public sealed class SmartBirdSurfaceFactory : IMptAvaloniaSurfaceFactory
{
    public Control CreateSurface(MptAvaloniaSurfaceContext context)
    {
        var host = new SmartBirdSurfaceHost();
        host.SetOwnedContent(CreateLoadingView());

        _ = PopulateAsync(host, context);
        return host;
    }

    private static async Task PopulateAsync(SmartBirdSurfaceHost host, MptAvaloniaSurfaceContext context)
    {
        try
        {
            host.SetOwnedContent(await CreateLoadedSurfaceAsync(context));
        }
        catch (Exception ex)
        {
            Info(context, $"SmartBird failed to load: {ex.Message}");
            host.SetOwnedContent(CreateFailureView(host, context, ex.Message));
        }
    }

    private static async Task<UserControl> CreateLoadedSurfaceAsync(MptAvaloniaSurfaceContext context)
    {
        var tools = new SmartBirdThermostatToolService();
        var snapshot = await tools.LoadAsync();
        var viewModel = new SmartBirdThermostatViewModel(
            snapshot,
            refresh: () => tools.LoadAsync(),
            startService: () => tools.StartScheduledServiceAsync(),
            embeddedBrowserSupported: context.WebSurfaces is not null);

        await viewModel.InitializeSettingsAsync();

        Info(context, snapshot.IsOnline
            ? $"SmartBird connected at {snapshot.DashboardUri}."
            : $"SmartBird offline at {snapshot.DashboardUri}.");

        return new SmartBirdThermostatView(
            context.WebSurfaces,
            context.ToolId,
            context.RouteId)
        {
            DataContext = viewModel
        };
    }

    private static Control CreateLoadingView() =>
        new Border
        {
            Padding = new Avalonia.Thickness(32),
            Child = new StackPanel
            {
                Spacing = 8,
                Children =
                {
                    new TextBlock { Text = "SmartBird 温度管理器", FontSize = 30, FontWeight = Avalonia.Media.FontWeight.SemiBold },
                    new TextBlock { Text = "正在连接管理页面与温控服务…" },
                    new ProgressBar { IsIndeterminate = true, Width = 240, HorizontalAlignment = Avalonia.Layout.HorizontalAlignment.Left }
                }
            }
        };

    private static Control CreateFailureView(
        SmartBirdSurfaceHost host,
        MptAvaloniaSurfaceContext context,
        string message)
    {
        var retry = new Button { Content = "重试", HorizontalAlignment = Avalonia.Layout.HorizontalAlignment.Left };
        retry.Click += (_, _) =>
        {
            host.SetOwnedContent(CreateLoadingView());
            _ = PopulateAsync(host, context);
        };

        return new Border
        {
            Padding = new Avalonia.Thickness(32),
            Child = new StackPanel
            {
                Spacing = 12,
                Children =
                {
                    new TextBlock { Text = "SmartBird 暂时无法连接", FontSize = 26, FontWeight = Avalonia.Media.FontWeight.SemiBold },
                    new TextBlock { Text = message, TextWrapping = Avalonia.Media.TextWrapping.Wrap },
                    retry
                }
            }
        };
    }

    private static void Info(MptAvaloniaSurfaceContext context, string message)
    {
        context.Log(new MptSurfaceLogEntry("info", message, DateTimeOffset.Now));
    }

    private sealed class SmartBirdSurfaceHost : ContentControl, IDisposable
    {
        private int _disposed;

        public void SetOwnedContent(Control content)
        {
            ObjectDisposedException.ThrowIf(Volatile.Read(ref _disposed) != 0, this);
            var previous = Content;
            Content = content;
            if (!ReferenceEquals(previous, content) && previous is IDisposable disposable)
            {
                disposable.Dispose();
            }
        }

        public void Dispose()
        {
            if (Interlocked.Exchange(ref _disposed, 1) != 0)
            {
                return;
            }
            var previous = Content;
            Content = null;
            if (previous is IDisposable disposable)
            {
                disposable.Dispose();
            }
        }
    }
}
