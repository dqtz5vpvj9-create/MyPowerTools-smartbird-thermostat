using Avalonia.Controls;
using MyPowerTools.AvaloniaSdk;

namespace SmartBird.Surface.Views;

internal sealed class SmartBirdWebSurfaceSessionController : IDisposable
{
    private readonly IMptWebSurfaceService? _webSurfaces;
    private readonly string _toolId;
    private readonly string _routeId;
    private readonly Action<Control?> _setView;
    private readonly Action<MptWebSurfaceState, string> _setState;
    private IMptWebSurfaceSession? _session;
    private Uri? _source;
    private int _disposed;

    public SmartBirdWebSurfaceSessionController(
        IMptWebSurfaceService? webSurfaces,
        string toolId,
        string routeId,
        Action<Control?> setView,
        Action<MptWebSurfaceState, string> setState)
    {
        _webSurfaces = webSurfaces;
        _toolId = toolId;
        _routeId = routeId;
        _setView = setView;
        _setState = setState;
    }

    public Uri? Source => _source;

    public void SetSource(Uri source)
    {
        ArgumentNullException.ThrowIfNull(source);
        if (_webSurfaces is null || Volatile.Read(ref _disposed) != 0)
        {
            return;
        }
        if (_session is not null && _source == source)
        {
            return;
        }

        ReleaseSession();
        try
        {
            var session = _webSurfaces.CreateSession(new MptWebSurfaceRequest(
                _toolId,
                _routeId,
                source,
                []));
            _source = source;
            _session = session;
            session.StateChanged += HandleStateChanged;
            _setView(session.View);
            _setState(session.State, "");
        }
        catch (Exception ex)
        {
            ReleaseSession();
            _setState(MptWebSurfaceState.Unavailable, ex.GetBaseException().Message);
        }
    }

    public void Reload(Uri source)
    {
        SetSource(source);
        _session?.Reload();
    }

    public void Clear() => ReleaseSession();

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) == 0)
        {
            ReleaseSession();
        }
    }

    private void HandleStateChanged(object? sender, MptWebSurfaceStateChangedEventArgs eventArguments) =>
        _setState(eventArguments.State, eventArguments.Message);

    private void ReleaseSession()
    {
        var session = _session;
        _session = null;
        _source = null;
        if (session is null)
        {
            return;
        }

        session.StateChanged -= HandleStateChanged;
        _setView(null);
        session.Dispose();
    }
}
