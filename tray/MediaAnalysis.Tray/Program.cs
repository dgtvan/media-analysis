namespace MediaAnalysis.Tray;

static class Program
{
    /// <summary>
    ///  The main entry point for the application.
    /// </summary>
    [STAThread]
    static int Main()
    {
        using var mutex = new Mutex(true, @"Local\MediaAnalysis-SingleInstance", out bool isFirstInstance);
        if (!isFirstInstance)
        {
            MessageBox.Show("Media Analysis is already running in the system tray.", "Media Analysis",
                MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 0;
        }

        ApplicationConfiguration.Initialize();
        // Errors in UI callbacks become a tray warning instead of a crash.
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
        Application.Run(new TrayApp());
        return 0;
    }
}
