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
        return CreateAsync(context).GetAwaiter().GetResult();
    }

    private static async Task<UserControl> CreateAsync(MptAvaloniaSurfaceContext context)
    {
        var tools = new SmartBirdThermostatToolService();
        var snapshot = await tools.LoadAsync();
        var viewModel = new SmartBirdThermostatViewModel(
            snapshot,
            refresh: () => tools.LoadAsync(),
            startService: () => tools.StartScheduledServiceAsync(),
            embeddedBrowserSupported: true);

        await viewModel.InitializeSettingsAsync();

        Info(context, snapshot.IsOnline
            ? $"SmartBird connected at {snapshot.DashboardUri}."
            : $"SmartBird offline at {snapshot.DashboardUri}.");

        return new SmartBirdThermostatView { DataContext = viewModel };
    }

    private static void Info(MptAvaloniaSurfaceContext context, string message)
    {
        context.Log(new MptSurfaceLogEntry("info", message, DateTimeOffset.Now));
    }
}
