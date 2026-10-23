namespace MediaAnalysis.Tray;

/// <summary>Where a project stands, as far as the files on disk tell.</summary>
enum ProjectState
{
    /// <summary>No video or audio files (yet), so there is nothing to analyse.</summary>
    Empty,
    /// <summary>Has media files but no report, or media files changed after the report was written.</summary>
    NotAnalysed,
    /// <summary>The report is newer than every media file.</summary>
    Analysed,
}

/// <summary>One subfolder of the projects folder and its output folder.</summary>
sealed record Project(
    string Name,
    string Path,
    DateTime LastModifiedUtc,
    int MediaFileCount,
    string OutputPath,
    DateTime? ReportWrittenUtc,
    ProjectState State,
    DateTime CreatedUtc = default,
    DateTime NewestMediaUtc = default)
{
    public string ReportPath => System.IO.Path.Combine(OutputPath, ProjectCatalog.ReportFileName);
}

/// <summary>
/// Reads the projects folder: each subfolder is a project whose output goes to <c>&lt;output folder&gt;\&lt;name&gt;</c>.
/// A project is analysed when its <c>report.md</c> is newer than all of its media files.
/// </summary>
static class ProjectCatalog
{
    public const string ReportFileName = "report.md";

    // Same lists as the core (src/media_analysis/config.py), which also only reads files at the top of the folder.
    private static readonly HashSet<string> MediaExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg", ".ts",
        ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".amr",
    };

    /// <summary>All projects, newest first. Missing or unreadable folders give an empty list.</summary>
    public static List<Project> Scan(string? projectsRoot, string? outputRoot)
    {
        if (string.IsNullOrEmpty(projectsRoot) || !Directory.Exists(projectsRoot))
            return [];
        string? outputFull = string.IsNullOrEmpty(outputRoot) ? null : Normalize(outputRoot);
        try
        {
            return new DirectoryInfo(projectsRoot).GetDirectories()
                .Where(d => (d.Attributes & (FileAttributes.Hidden | FileAttributes.System)) == 0 && !d.Name.StartsWith('.'))
                // The output folder may sit inside the projects folder; it is not a project.
                .Where(d => outputFull == null || !string.Equals(Normalize(d.FullName), outputFull, StringComparison.OrdinalIgnoreCase))
                .Select(d => Read(d, outputRoot))
                .OrderByDescending(p => p.LastModifiedUtc)
                .ToList();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return [];
        }
    }

    /// <summary>Reads one project folder.</summary>
    public static Project Read(DirectoryInfo folder, string? outputRoot)
    {
        DateTime lastModified = folder.LastWriteTimeUtc;
        DateTime newestMedia = DateTime.MinValue;
        int mediaCount = 0;
        try
        {
            foreach (var file in folder.GetFiles())
            {
                // A copied file keeps its original write time but gets a new creation time.
                DateTime changed = file.LastWriteTimeUtc > file.CreationTimeUtc ? file.LastWriteTimeUtc : file.CreationTimeUtc;
                if (changed > lastModified)
                    lastModified = changed;
                if (MediaExtensions.Contains(file.Extension))
                {
                    mediaCount++;
                    if (changed > newestMedia)
                        newestMedia = changed;
                }
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
        }

        string outputPath = string.IsNullOrEmpty(outputRoot) ? "" : Path.Combine(outputRoot, folder.Name);
        string report = outputPath.Length > 0 ? Path.Combine(outputPath, ReportFileName) : "";
        DateTime? reportWritten = report.Length > 0 && File.Exists(report) ? File.GetLastWriteTimeUtc(report) : null;

        ProjectState state = mediaCount == 0 ? ProjectState.Empty
            : reportWritten > newestMedia ? ProjectState.Analysed
            : ProjectState.NotAnalysed;
        return new Project(folder.Name, folder.FullName, lastModified, mediaCount, outputPath, reportWritten, state,
            folder.CreationTimeUtc, newestMedia);
    }

    private static string Normalize(string path) => Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar);
}
