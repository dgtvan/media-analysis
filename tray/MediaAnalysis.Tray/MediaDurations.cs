using System.Diagnostics;
using System.Globalization;

namespace MediaAnalysis.Tray;

/// <summary>
/// How long each project's recordings are, measured with ffprobe in the background and remembered per file
/// (path, size and write time), so each file is probed once while the app runs.
/// </summary>
/// <remarks>
/// A project's length is the total of its video files, or of its audio files when it has no video. Meeting Recorder's
/// microphone and system audio (<c>audio-mic.*</c>, <c>audio-system.*</c>) record the same session, so that pair
/// counts once, as the longer of the two; with a video next to them only the video counts.
/// </remarks>
sealed class MediaDurations : IDisposable
{
    private readonly Func<string, TimeSpan?> _probe;
    private readonly Dictionary<MediaFile, TimeSpan?> _known = [];
    private readonly Queue<MediaFile> _queue = new();
    private readonly HashSet<MediaFile> _queued = [];
    private readonly object _lock = new();
    private readonly CancellationTokenSource _stop = new();
    private Task? _worker;

    /// <summary>Raised on a background thread when a file has been measured.</summary>
    public event Action? Changed;

    /// <param name="ffprobe">Finds ffprobe.exe; null when it isn't available.</param>
    public MediaDurations(Func<string?> ffprobe) : this(path => Probe(ffprobe(), path))
    {
    }

    /// <param name="probe">Measures one file; null when it can't.</param>
    internal MediaDurations(Func<string, TimeSpan?> probe) => _probe = probe;

    /// <summary>
    /// The project's length for the grid: <c>1:02:03</c> or <c>12:34</c>, <c>measuring...</c> while files are still
    /// being probed, <c>?</c> when none could be measured, empty without media.
    /// </summary>
    public string Describe(Project project)
    {
        var files = project.Media.Any(f => f.IsVideo) ? project.Media.Where(f => f.IsVideo) : project.Media;
        TimeSpan total = TimeSpan.Zero, session = TimeSpan.Zero;
        bool pending = false, any = false, failed = false;
        lock (_lock)
        {
            foreach (var file in files)
            {
                any = true;
                if (!_known.TryGetValue(file, out var length))
                {
                    pending = true;
                    if (_queued.Add(file))
                        _queue.Enqueue(file);
                }
                else if (length is not { } l)
                    failed = true;
                else if (IsSessionAudio(file))
                    session = l > session ? l : session;
                else
                    total += l;
            }
            total += session;
            if (pending && _worker is null or { IsCompleted: true })
                _worker = Task.Run(Work);
        }
        if (!any)
            return "";
        if (pending)
            return "measuring...";
        return failed && total == TimeSpan.Zero ? "?" : Format(total);
    }

    private static bool IsSessionAudio(MediaFile file) =>
        Path.GetFileNameWithoutExtension(file.Path) is var name
        && (name.Equals("audio-mic", StringComparison.OrdinalIgnoreCase) || name.Equals("audio-system", StringComparison.OrdinalIgnoreCase));

    internal static string Format(TimeSpan length) =>
        length.TotalHours >= 1 ? $"{(int)length.TotalHours}:{length:mm\\:ss}" : $"{(int)length.TotalMinutes}:{length:ss}";

    /// <summary>Parses ffprobe's <c>format=duration</c> output (seconds).</summary>
    internal static TimeSpan? ParseSeconds(string output) =>
        double.TryParse(output.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out double seconds) && seconds >= 0
            ? TimeSpan.FromSeconds(Math.Round(seconds))
            : null;

    private void Work()
    {
        while (!_stop.IsCancellationRequested)
        {
            MediaFile file;
            lock (_lock)
            {
                if (_queue.Count == 0)
                    return;
                file = _queue.Dequeue();
            }
            TimeSpan? length = _probe(file.Path);
            lock (_lock)
            {
                _known[file] = length;
                _queued.Remove(file);
            }
            Changed?.Invoke();
        }
    }

    private static TimeSpan? Probe(string? ffprobe, string path)
    {
        if (ffprobe == null)
            return null;
        try
        {
            var info = new ProcessStartInfo(ffprobe)
            {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
            };
            foreach (string arg in new[] { "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path })
                info.ArgumentList.Add(arg);
            using var process = Process.Start(info)!;
            var output = process.StandardOutput.ReadToEndAsync();
            _ = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(30_000))
            {
                process.Kill();
                return null;
            }
            return process.ExitCode == 0 ? ParseSeconds(output.Result) : null;
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException or IOException)
        {
            return null;
        }
    }

    public void Dispose()
    {
        _stop.Cancel();
        _stop.Dispose();
    }
}
