using System.Diagnostics;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Platform;
using Avalonia.Threading;
using Avalonia.VisualTree;

namespace MyPowerTools.Shell.Avalonia.Views;

public enum SmartBirdWebViewState
{
    Loading,
    Ready,
    Unavailable,
    Failed
}

public sealed class SmartBirdWebViewStateChangedEventArgs(
    SmartBirdWebViewState state,
    string message = "") : EventArgs
{
    public SmartBirdWebViewState State { get; } = state;
    public string Message { get; } = message;
}

public static class SmartBirdWebNavigationPolicy
{
    public static bool IsSupportedWebUri(Uri? uri)
    {
        return MyPowerTools.Shell.Avalonia.Services.SmartBirdThermostatToolService
            .IsSupportedDashboardOrigin(uri);
    }

    public static bool HasSameOrigin(Uri allowed, Uri target)
    {
        return IsSupportedWebUri(allowed) &&
               IsSupportedWebUri(target) &&
               string.Equals(allowed.Scheme, target.Scheme, StringComparison.OrdinalIgnoreCase) &&
               string.Equals(allowed.Host, target.Host, StringComparison.OrdinalIgnoreCase) &&
               allowed.Port == target.Port;
    }
}

public sealed class SmartBirdWebView : Control
{
    private const int MaximumHostFrameLength = 16 * 1024;
    public static readonly StyledProperty<Uri?> SourceProperty =
        AvaloniaProperty.Register<SmartBirdWebView, Uri?>(nameof(Source));

    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private Process? _hostProcess;
    private StreamWriter? _hostInput;
    private CancellationTokenSource? _hostCancellation;
    private TopLevel? _topLevel;
    private HostBounds? _lastBounds;
    private int _generation;
    private bool _attached;
    private bool _terminalStateReported;

    public SmartBirdWebView()
    {
        LayoutUpdated += (_, _) => UpdateHostBounds();
    }

    public event EventHandler<SmartBirdWebViewStateChangedEventArgs>? StateChanged;

    public Uri? Source
    {
        get => GetValue(SourceProperty);
        set => SetValue(SourceProperty, value);
    }

    public void Reload()
    {
        if (!_attached || !IsVisible)
        {
            return;
        }

        if (_hostProcess is { HasExited: false })
        {
            NotifyState(SmartBirdWebViewState.Loading);
            _ = SendCommandAsync(new { type = "reload" }, _generation);
            return;
        }

        StartHost();
    }

    public static string ResolveWebToolHostPath(string applicationBaseDirectory)
    {
        return Path.Combine(
            Path.GetFullPath(applicationBaseDirectory),
            "WebToolHost",
            "MyPowerTools.WebToolHost.exe");
    }

    protected override void OnAttachedToVisualTree(VisualTreeAttachmentEventArgs eventArguments)
    {
        base.OnAttachedToVisualTree(eventArguments);
        _attached = true;
        _topLevel = TopLevel.GetTopLevel(this);
        NativeWebSurfaceCoordinator.VisibilityChanged += OnNativeSurfaceVisibilityChanged;
        StartHostIfEligible();
    }

    protected override void OnDetachedFromVisualTree(VisualTreeAttachmentEventArgs eventArguments)
    {
        _attached = false;
        NativeWebSurfaceCoordinator.VisibilityChanged -= OnNativeSurfaceVisibilityChanged;
        _topLevel = null;
        StopHost();
        base.OnDetachedFromVisualTree(eventArguments);
    }

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if (change.Property == SourceProperty)
        {
            if (!SmartBirdWebNavigationPolicy.IsSupportedWebUri(change.GetNewValue<Uri?>()))
            {
                StopHost();
                NotifyState(
                    SmartBirdWebViewState.Unavailable,
                    "SmartBird 控制台仅允许使用已保存的本机 HTTP 端点。");
                return;
            }
            StopHost();
            StartHostIfEligible();
        }
        else if (change.Property == IsVisibleProperty)
        {
            if (IsVisible)
            {
                StartHostIfEligible();
            }
            else
            {
                StopHost();
            }
        }
    }

    protected override void OnGotFocus(FocusChangedEventArgs eventArguments)
    {
        base.OnGotFocus(eventArguments);
        var direction = eventArguments.NavigationMethod == NavigationMethod.Tab
            ? eventArguments.KeyModifiers.HasFlag(KeyModifiers.Shift) ? "previous" : "next"
            : "programmatic";
        _ = SendCommandAsync(new { type = "focus", direction }, _generation);
    }

    private void OnNativeSurfaceVisibilityChanged(object? sender, EventArgs eventArguments)
    {
        UpdateHostBounds();
    }

    private void StartHostIfEligible()
    {
        if (!_attached || !IsVisible || !SmartBirdWebNavigationPolicy.IsSupportedWebUri(Source))
        {
            return;
        }
        if (_hostProcess is { HasExited: false })
        {
            UpdateHostBounds();
            return;
        }
        StartHost();
    }

    private void StartHost()
    {
        if (!OperatingSystem.IsWindows())
        {
            NotifyState(
                SmartBirdWebViewState.Unavailable,
                "当前渲染环境无法启动 Windows WebView2 宿主。可在系统浏览器中打开控制台。");
            return;
        }

        _topLevel ??= TopLevel.GetTopLevel(this);
        var platformHandle = _topLevel?.TryGetPlatformHandle();
        if (platformHandle is null ||
            !string.Equals(platformHandle.HandleDescriptor, "HWND", StringComparison.OrdinalIgnoreCase) ||
            platformHandle.Handle == 0)
        {
            NotifyState(
                SmartBirdWebViewState.Unavailable,
                "当前渲染环境无法取得 Windows 窗口句柄。可在系统浏览器中打开控制台。");
            return;
        }

        var executablePath = ResolveWebToolHostPath(AppContext.BaseDirectory);
        if (!File.Exists(executablePath))
        {
            NotifyState(
                SmartBirdWebViewState.Unavailable,
                "SmartBird 独立 WebView 宿主尚未安装。可在系统浏览器中打开控制台。");
            return;
        }

        StopHost();
        var generation = ++_generation;
        var cancellation = new CancellationTokenSource();
        var startInfo = new ProcessStartInfo
        {
            FileName = executablePath,
            WorkingDirectory = Path.GetDirectoryName(executablePath)!,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        startInfo.ArgumentList.Add("--tool");
        startInfo.ArgumentList.Add("smartbird");
        startInfo.ArgumentList.Add("--parent-hwnd");
        startInfo.ArgumentList.Add(platformHandle.Handle.ToInt64().ToString(CultureInfo.InvariantCulture));
        startInfo.ArgumentList.Add("--parent-pid");
        startInfo.ArgumentList.Add(Environment.ProcessId.ToString(CultureInfo.InvariantCulture));
        startInfo.ArgumentList.Add("--source");
        startInfo.ArgumentList.Add(Source!.AbsoluteUri);

        try
        {
            var process = Process.Start(startInfo);
            if (process is null)
            {
                cancellation.Dispose();
                NotifyState(
                    SmartBirdWebViewState.Failed,
                    "SmartBird 独立 WebView 宿主无法启动。Shell 仍可继续使用。");
                return;
            }

            process.EnableRaisingEvents = true;
            process.Exited += (_, _) => Dispatcher.UIThread.Post(() => HandleHostExit(process, generation));
            _hostProcess = process;
            _hostInput = process.StandardInput;
            _hostCancellation = cancellation;
            _terminalStateReported = false;
            _lastBounds = null;
            NotifyState(SmartBirdWebViewState.Loading);
            _ = ReadHostEventsAsync(process, generation, cancellation.Token);
            _ = DrainHostErrorsAsync(process, cancellation.Token);
            UpdateHostBounds();
        }
        catch (Exception ex)
        {
            cancellation.Dispose();
            NotifyState(SmartBirdWebViewState.Failed, FriendlyHostError(ex));
        }
    }

    private async Task ReadHostEventsAsync(
        Process process,
        int generation,
        CancellationToken cancellationToken)
    {
        try
        {
            await foreach (var line in ReadBoundedFramesAsync(
                               process.StandardOutput.BaseStream,
                               cancellationToken).ConfigureAwait(false))
            {
                if (!TryReadHostEvent(line, process.Id, out var hostEvent))
                {
                    Dispatcher.UIThread.Post(() => HandleInvalidHostProtocol(process, generation));
                    return;
                }
                Dispatcher.UIThread.Post(() => HandleHostEvent(process, generation, hostEvent));
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (IOException)
        {
        }
        catch (ObjectDisposedException)
        {
        }
        catch (InvalidDataException)
        {
            Dispatcher.UIThread.Post(() => HandleInvalidHostProtocol(process, generation));
        }
    }

    private static async Task DrainHostErrorsAsync(Process process, CancellationToken cancellationToken)
    {
        try
        {
            var buffer = new byte[4096];
            while (await process.StandardError.BaseStream.ReadAsync(buffer, cancellationToken).ConfigureAwait(false) > 0)
            {
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (IOException)
        {
        }
        catch (ObjectDisposedException)
        {
        }
    }

    private void HandleHostEvent(
        Process process,
        int generation,
        HostProcessEvent hostEvent)
    {
        if (generation != _generation || !ReferenceEquals(process, _hostProcess))
        {
            return;
        }
        if (hostEvent.Kind == HostProcessEventKind.Shortcut)
        {
            if (_topLevel is MainWindow window)
            {
                _ = window.HandleForwardedWebToolShortcutAsync(hostEvent.Value);
            }
            return;
        }
        if (hostEvent.Kind == HostProcessEventKind.FocusMove)
        {
            MoveFocusBackToShell(hostEvent.Value);
            return;
        }
        if (hostEvent.State is SmartBirdWebViewState.Failed or SmartBirdWebViewState.Unavailable)
        {
            _terminalStateReported = true;
        }
        NotifyState(hostEvent.State, hostEvent.Message);
    }

    private void HandleInvalidHostProtocol(Process process, int generation)
    {
        if (generation != _generation || !ReferenceEquals(process, _hostProcess))
        {
            return;
        }
        _terminalStateReported = true;
        NotifyState(
            SmartBirdWebViewState.Failed,
            "SmartBird 独立宿主发送了超出限制的协议帧，连接已关闭。Shell 仍可继续使用。");
        StopHost();
    }

    private void MoveFocusBackToShell(string direction)
    {
        var navigationDirection = string.Equals(direction, "previous", StringComparison.Ordinal)
            ? NavigationDirection.Previous
            : NavigationDirection.Next;
        _topLevel?.FocusManager?.TryMoveFocus(
            navigationDirection,
            new FindNextElementOptions { FocusedElement = this });
    }

    private void HandleHostExit(Process process, int generation)
    {
        if (generation != _generation || !ReferenceEquals(process, _hostProcess))
        {
            return;
        }

        _hostProcess = null;
        _hostInput = null;
        _hostCancellation?.Cancel();
        _hostCancellation?.Dispose();
        _hostCancellation = null;
        _lastBounds = null;
        process.Dispose();
        if (_attached && IsVisible && !_terminalStateReported)
        {
            NotifyState(
                SmartBirdWebViewState.Failed,
                "SmartBird 独立 WebView 宿主已退出。Shell 仍在运行，可重试或在系统浏览器中打开控制台。");
        }
    }

    private void UpdateHostBounds()
    {
        if (!_attached || _hostProcess is not { HasExited: false } || _topLevel is null)
        {
            return;
        }
        var origin = this.TranslatePoint(new Point(0, 0), _topLevel);
        if (origin is null)
        {
            return;
        }

        var controlRect = new Rect(origin.Value, Bounds.Size);
        var visibleRect = Intersect(
            controlRect,
            new Rect(0, 0, _topLevel.ClientSize.Width, _topLevel.ClientSize.Height));
        var ancestor = this.GetVisualParent();
        while (ancestor is not null && !ReferenceEquals(ancestor, _topLevel))
        {
            if (ancestor.ClipToBounds &&
                ancestor.TranslatePoint(new Point(0, 0), _topLevel) is { } ancestorOrigin)
            {
                visibleRect = Intersect(visibleRect, new Rect(ancestorOrigin, ancestor.Bounds.Size));
            }
            if (ancestor.Clip is { } clip &&
                ancestor.TranslatePoint(clip.Bounds.Position, _topLevel) is { } clipOrigin)
            {
                visibleRect = Intersect(visibleRect, new Rect(clipOrigin, clip.Bounds.Size));
            }
            ancestor = ancestor.GetVisualParent();
        }

        var scaling = _topLevel.RenderScaling;
        var bounds = new HostBounds(
            X: (int)Math.Round(controlRect.X * scaling),
            Y: (int)Math.Round(controlRect.Y * scaling),
            Width: Math.Max(1, (int)Math.Round(controlRect.Width * scaling)),
            Height: Math.Max(1, (int)Math.Round(controlRect.Height * scaling)),
            ClipX: Math.Max(0, (int)Math.Round((visibleRect.X - controlRect.X) * scaling)),
            ClipY: Math.Max(0, (int)Math.Round((visibleRect.Y - controlRect.Y) * scaling)),
            ClipWidth: Math.Max(0, (int)Math.Round(visibleRect.Width * scaling)),
            ClipHeight: Math.Max(0, (int)Math.Round(visibleRect.Height * scaling)),
            Visible: IsVisible &&
                     !NativeWebSurfaceCoordinator.ShellOverlayVisible &&
                     visibleRect.Width > 0 &&
                     visibleRect.Height > 0);
        if (bounds == _lastBounds)
        {
            return;
        }
        _lastBounds = bounds;
        _ = SendCommandAsync(new
        {
            type = "bounds",
            x = bounds.X,
            y = bounds.Y,
            width = bounds.Width,
            height = bounds.Height,
            clipX = bounds.ClipX,
            clipY = bounds.ClipY,
            clipWidth = bounds.ClipWidth,
            clipHeight = bounds.ClipHeight,
            visible = bounds.Visible
        }, _generation);
    }

    private async Task SendCommandAsync(object command, int generation)
    {
        var writer = _hostInput;
        if (writer is null || generation != _generation)
        {
            return;
        }
        var payload = JsonSerializer.Serialize(command);
        await _writeLock.WaitAsync().ConfigureAwait(false);
        try
        {
            if (generation == _generation && ReferenceEquals(writer, _hostInput))
            {
                await writer.WriteLineAsync(payload).ConfigureAwait(false);
                await writer.FlushAsync().ConfigureAwait(false);
            }
        }
        catch (IOException)
        {
        }
        catch (ObjectDisposedException)
        {
        }
        catch (InvalidOperationException)
        {
        }
        finally
        {
            _writeLock.Release();
        }
    }

    private void StopHost()
    {
        var process = _hostProcess;
        var writer = _hostInput;
        var cancellation = _hostCancellation;
        _generation++;
        _hostProcess = null;
        _hostInput = null;
        _hostCancellation = null;
        _terminalStateReported = false;
        _lastBounds = null;
        cancellation?.Cancel();
        cancellation?.Dispose();
        if (process is not null)
        {
            _ = StopHostAsync(process, writer);
        }
    }

    private async Task StopHostAsync(Process process, StreamWriter? writer)
    {
        try
        {
            if (!process.HasExited && writer is not null)
            {
                await _writeLock.WaitAsync().ConfigureAwait(false);
                try
                {
                    await writer.WriteLineAsync("{\"type\":\"shutdown\"}").ConfigureAwait(false);
                    await writer.FlushAsync().ConfigureAwait(false);
                    writer.Close();
                }
                finally
                {
                    _writeLock.Release();
                }
            }
            if (!process.HasExited)
            {
                await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false);
            }
        }
        catch (TimeoutException)
        {
            TryKill(process);
        }
        catch (InvalidOperationException)
        {
        }
        catch (IOException)
        {
            TryKill(process);
        }
        finally
        {
            if (!process.HasExited)
            {
                TryKill(process);
            }
            process.Dispose();
        }
    }

    private static void TryKill(Process process)
    {
        try
        {
            process.Kill(entireProcessTree: true);
        }
        catch
        {
        }
    }

    private static async IAsyncEnumerable<string> ReadBoundedFramesAsync(
        Stream stream,
        [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        var readBuffer = new byte[1024];
        var frame = new List<byte>(1024);
        while (true)
        {
            var bytesRead = await stream.ReadAsync(readBuffer, cancellationToken).ConfigureAwait(false);
            if (bytesRead == 0)
            {
                yield break;
            }
            for (var index = 0; index < bytesRead; index++)
            {
                var value = readBuffer[index];
                if (value == (byte)'\n')
                {
                    if (frame.Count > 0 && frame[^1] == (byte)'\r')
                    {
                        frame.RemoveAt(frame.Count - 1);
                    }
                    yield return Encoding.UTF8.GetString(frame.ToArray());
                    frame.Clear();
                    continue;
                }
                if (frame.Count >= MaximumHostFrameLength)
                {
                    throw new InvalidDataException("WebToolHost protocol frame exceeded its limit.");
                }
                frame.Add(value);
            }
        }
    }

    private static bool TryReadHostEvent(
        string line,
        int expectedProcessId,
        out HostProcessEvent hostEvent)
    {
        hostEvent = new HostProcessEvent(HostProcessEventKind.State, SmartBirdWebViewState.Loading, "", "");
        try
        {
            using var payload = JsonDocument.Parse(line);
            var root = payload.RootElement;
            if (root.ValueKind != JsonValueKind.Object ||
                !root.TryGetProperty("type", out var typeNode) ||
                typeNode.ValueKind != JsonValueKind.String ||
                !root.TryGetProperty("protocolVersion", out var versionNode) ||
                versionNode.ValueKind != JsonValueKind.Number ||
                !versionNode.TryGetInt32(out var protocolVersion) ||
                protocolVersion != 1 ||
                !root.TryGetProperty("pid", out var processNode) ||
                processNode.ValueKind != JsonValueKind.Number ||
                !processNode.TryGetInt32(out var processId) ||
                processId != expectedProcessId)
            {
                return false;
            }

            var type = typeNode.GetString();
            if (string.Equals(type, "shortcut", StringComparison.Ordinal) &&
                root.TryGetProperty("gesture", out var gestureNode) &&
                gestureNode.ValueKind == JsonValueKind.String)
            {
                var gesture = gestureNode.GetString() ?? "";
                if (gesture.Length is > 0 and <= 32)
                {
                    hostEvent = new HostProcessEvent(
                        HostProcessEventKind.Shortcut,
                        SmartBirdWebViewState.Ready,
                        "",
                        gesture);
                    return true;
                }
                return false;
            }
            if (string.Equals(type, "focusMove", StringComparison.Ordinal) &&
                root.TryGetProperty("direction", out var directionNode) &&
                directionNode.ValueKind == JsonValueKind.String)
            {
                var direction = directionNode.GetString() ?? "";
                if (direction is "next" or "previous")
                {
                    hostEvent = new HostProcessEvent(
                        HostProcessEventKind.FocusMove,
                        SmartBirdWebViewState.Ready,
                        "",
                        direction);
                    return true;
                }
                return false;
            }
            if (!string.Equals(type, "state", StringComparison.Ordinal) ||
                !root.TryGetProperty("state", out var stateNode) ||
                stateNode.ValueKind != JsonValueKind.String)
            {
                return false;
            }
            var state = stateNode.GetString() switch
            {
                "ready" => SmartBirdWebViewState.Ready,
                "unavailable" => SmartBirdWebViewState.Unavailable,
                "failed" => SmartBirdWebViewState.Failed,
                _ => SmartBirdWebViewState.Loading
            };
            var message = "";
            if (root.TryGetProperty("message", out var messageNode))
            {
                if (messageNode.ValueKind != JsonValueKind.String)
                {
                    return false;
                }
                message = messageNode.GetString() ?? "";
            }
            if (message.Length > 1024)
            {
                return false;
            }
            hostEvent = new HostProcessEvent(HostProcessEventKind.State, state, message, "");
            return true;
        }
        catch (JsonException)
        {
            return false;
        }
        catch (InvalidOperationException)
        {
            return false;
        }
        catch (FormatException)
        {
            return false;
        }
    }

    private void NotifyState(SmartBirdWebViewState state, string message = "")
    {
        void Raise() => StateChanged?.Invoke(this, new SmartBirdWebViewStateChangedEventArgs(state, message));
        if (Dispatcher.UIThread.CheckAccess())
        {
            Raise();
        }
        else
        {
            Dispatcher.UIThread.Post(Raise);
        }
    }

    private static string FriendlyHostError(Exception exception)
    {
        return exception switch
        {
            UnauthorizedAccessException => "SmartBird 独立 WebView 宿主无法启动。请检查安装目录权限。",
            _ => "SmartBird 独立 WebView 宿主启动失败。Shell 仍可继续使用，可重试或在系统浏览器中打开控制台。"
        };
    }

    private static Rect Intersect(Rect left, Rect right)
    {
        var x = Math.Max(left.Left, right.Left);
        var y = Math.Max(left.Top, right.Top);
        var maxX = Math.Min(left.Right, right.Right);
        var maxY = Math.Min(left.Bottom, right.Bottom);
        return maxX <= x || maxY <= y
            ? new Rect(x, y, 0, 0)
            : new Rect(x, y, maxX - x, maxY - y);
    }

    private enum HostProcessEventKind
    {
        State,
        Shortcut,
        FocusMove
    }

    private sealed record HostProcessEvent(
        HostProcessEventKind Kind,
        SmartBirdWebViewState State,
        string Message,
        string Value);

    private sealed record HostBounds(
        int X,
        int Y,
        int Width,
        int Height,
        int ClipX,
        int ClipY,
        int ClipWidth,
        int ClipHeight,
        bool Visible);
}
