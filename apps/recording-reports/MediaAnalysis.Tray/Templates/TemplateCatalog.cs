using System.Text.RegularExpressions;

namespace MediaAnalysis.Tray;

/// <summary>One report template file (see core/src/media_analysis/workflows/report/templates/registry.py for the format).</summary>
sealed record TemplateInfo(string Id, string Name, string Description, string Path, bool BuiltIn, bool Fallback, bool Overridden);

/// <summary>
/// Lists the built-in templates shipped with the core and the user's own, reading only the front-matter fields the
/// window shows. A user template with the same id as a built-in replaces it (the core does the same).
/// </summary>
static partial class TemplateCatalog
{
    public static string UserDir => AppPaths.Templates;

    public static List<TemplateInfo> Load(string? builtinDir, string userDir)
    {
        var user = Read(userDir, builtIn: false);
        var userIds = user.Select(t => t.Id).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var builtin = Read(builtinDir, builtIn: true)
            .Select(t => t with { Overridden = userIds.Contains(t.Id) });
        return [.. user.Concat(builtin).OrderBy(t => t.Fallback).ThenBy(t => t.Id, StringComparer.OrdinalIgnoreCase)];
    }

    private static List<TemplateInfo> Read(string? dir, bool builtIn)
    {
        if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir))
            return [];
        var list = new List<TemplateInfo>();
        foreach (string path in Directory.GetFiles(dir, "*.md"))
        {
            try
            {
                var front = FrontMatter().Match(File.ReadAllText(path));
                if (!front.Success)
                    continue;
                string Field(string name) => Regex.Match(front.Groups[1].Value, $@"^{name}:\s*(.+?)\s*$", RegexOptions.Multiline)
                    .Groups[1].Value.Trim('"', '\'');
                string id = Field("id");
                if (id.Length == 0)
                    continue;
                list.Add(new TemplateInfo(id, Field("name"), Field("description"), path, builtIn,
                    Field("fallback").Equals("true", StringComparison.OrdinalIgnoreCase), false));
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
            }
        }
        return list;
    }

    [GeneratedRegex(@"\A﻿?---\s*\r?\n(.*?)\r?\n---", RegexOptions.Singleline)]
    private static partial Regex FrontMatter();
}
