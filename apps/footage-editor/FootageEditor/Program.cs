namespace FootageEditor;

static class Program
{
    public const string AppName = "Footage Editor";
    public const string AppId = "FootageEditor";

    [STAThread]
    static int Main(string[] args)
    {
        // App deployment standard 4.3: before anything else; a second launch brings the first window forward.
        using var mutex = new Mutex(true, $@"Local\{AppId}-SingleInstance", out bool isFirstInstance);
        if (!isFirstInstance)
        {
            using var show = EventWaitHandle.TryOpenExisting($@"Local\{AppId}-Show", out var handle) ? handle : null;
            show?.Set();
            return 0;
        }

        ApplicationConfiguration.Initialize();
        string? dataDir = DataDirArgument(args);
        if (dataDir == null)
        {
            MessageBox.Show($"{AppName} was started without --data-dir. Start it from the Start Menu shortcut.",
                AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }
        Directory.CreateDirectory(dataDir);
        Application.Run(new MainWindow(Path.GetFullPath(dataDir)));
        return 0;
    }

    /// <summary>The value after <c>--data-dir</c>, or null when it is missing or empty.</summary>
    internal static string? DataDirArgument(string[] args)
    {
        int i = Array.FindIndex(args, a => a.Equals("--data-dir", StringComparison.OrdinalIgnoreCase));
        return i >= 0 && i + 1 < args.Length && !string.IsNullOrWhiteSpace(args[i + 1]) ? args[i + 1] : null;
    }
}
