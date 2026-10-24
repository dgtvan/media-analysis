using System.Diagnostics;

namespace MediaAnalysis.Tray;

/// <summary>
/// Opens a report in a new VS Code window on its output folder, with Claude Code signed in to the configured profile.
/// </summary>
static class ReportOpener
{
    /// <summary>Opens <paramref name="report"/>; returns an error message when it couldn't.</summary>
    public static string? Open(string report, string claudeProfile)
    {
        if (!File.Exists(report))
            return $"The report doesn't exist any more:\n{report}";
        try
        {
            if (FindVsCode() is { } code)
            {
                Process.Start(VsCodeCommand(code, report, claudeProfile));
                return null;
            }
            Process.Start(new ProcessStartInfo(report) { UseShellExecute = true });
            return null;
        }
        catch (System.ComponentModel.Win32Exception ex)
        {
            return $"The report couldn't be opened ({ex.Message}). Install VS Code, or choose an app for .md files in Windows.\n{report}";
        }
    }

    /// <summary>
    /// <c>code --new-window &lt;folder&gt; &lt;report&gt;</c>. VS Code gives a window opened from its command line the
    /// command's environment, so the Claude Code extension in that window uses CLAUDE_CONFIG_DIR.
    /// </summary>
    internal static ProcessStartInfo VsCodeCommand(string code, string report, string claudeProfile)
    {
        var info = new ProcessStartInfo(code) { UseShellExecute = false, CreateNoWindow = true };
        info.ArgumentList.Add("--new-window");
        info.ArgumentList.Add(Path.GetDirectoryName(Path.GetFullPath(report))!);
        info.ArgumentList.Add(report);
        info.Environment["CLAUDE_CONFIG_DIR"] = claudeProfile;
        return info;
    }

    /// <summary>VS Code's <c>code.cmd</c>: on the PATH, else in its default install folders.</summary>
    internal static string? FindVsCode()
    {
        var dirs = new List<string>();
        foreach (var target in new[] { EnvironmentVariableTarget.Process, EnvironmentVariableTarget.User, EnvironmentVariableTarget.Machine })
            dirs.AddRange((Environment.GetEnvironmentVariable("PATH", target) ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries));
        dirs.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Microsoft VS Code\bin"));
        dirs.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"Microsoft VS Code\bin"));
        foreach (string dir in dirs)
        {
            try
            {
                string path = Path.Combine(Environment.ExpandEnvironmentVariables(dir.Trim('"')), "code.cmd");
                if (File.Exists(path))
                    return path;
            }
            catch (ArgumentException)
            {
            }
        }
        return null;
    }
}
