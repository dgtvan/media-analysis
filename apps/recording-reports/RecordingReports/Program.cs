namespace RecordingReports;

static class Program
{
    /// <summary>
    ///  The main entry point for the application.
    /// </summary>
    [STAThread]
    static int Main(string[] args)
    {
        // App deployment standard 4.3: before anything else; a second launch exits silently.
        using var mutex = new Mutex(true, @"Local\RecordingReports-SingleInstance", out bool isFirstInstance);
        if (!isFirstInstance)
            return 0;

        ApplicationConfiguration.Initialize();
        string? dataDir = DataDirArgument(args);
        if (dataDir == null)
        {
            MessageBox.Show("Recording Reports was started without --data-dir. Start it from the Start Menu shortcut.",
                BuildInfo.AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }
        AppPaths.Initialize(dataDir);

        // Errors in UI callbacks become a tray warning instead of a crash.
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
        Application.Run(new TrayApp());
        return 0;
    }

    /// <summary>The value after <c>--data-dir</c>, or null when it is missing or empty.</summary>
    internal static string? DataDirArgument(string[] args)
    {
        int i = Array.FindIndex(args, a => a.Equals("--data-dir", StringComparison.OrdinalIgnoreCase));
        return i >= 0 && i + 1 < args.Length && !string.IsNullOrWhiteSpace(args[i + 1]) ? args[i + 1] : null;
    }
}
