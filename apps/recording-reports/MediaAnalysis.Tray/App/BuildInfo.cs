namespace MediaAnalysis.Tray;

/// <summary>Identifies the running build without version stamping (the build scripts know no versions).</summary>
static class BuildInfo
{
    /// <summary>When the exe was built, in local time.</summary>
    public static string Built
    {
        get
        {
            try
            {
                return File.GetLastWriteTime(Environment.ProcessPath ?? typeof(BuildInfo).Assembly.Location).ToString("yyyy-MM-dd HH:mm");
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException)
            {
                return "unknown";
            }
        }
    }
}
