using System.Diagnostics;
using System.Text;

namespace RecordingReports.UI;

/// <summary>
/// Shows one analysis log from its first line and keeps following it while the analysis runs. "Readable" shows
/// stages, warnings, errors and the performance summary; "Full" shows every line as written.
/// </summary>
sealed class LogWindow : Form
{
    private readonly string _path;
    private readonly TextBox _text;
    private readonly RadioButton _readable;
    private readonly System.Windows.Forms.Timer _follow = new() { Interval = 500 };
    private readonly List<string> _lines = [];
    private long _offset;
    private string _partial = "";

    public LogWindow(string path, string title)
    {
        _path = path;
        Text = $"Log: {title}";
        Size = new Size(1000, 650);
        StartPosition = FormStartPosition.CenterScreen;
        Icon = TrayIcons.Idle;

        _readable = new RadioButton { Text = "Readable", Checked = true, AutoSize = true };
        var full = new RadioButton { Text = "Full", AutoSize = true };
        _readable.CheckedChanged += (_, _) => Render();
        var openFile = new Button { Text = "Open log file", AutoSize = true };
        openFile.Click += (_, _) => Process.Start(new ProcessStartInfo(_path) { UseShellExecute = true });
        var bar = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(6) };
        bar.Controls.AddRange([_readable, full, openFile, new Label { Text = path, AutoSize = true, Padding = new Padding(8, 6, 0, 0), ForeColor = SystemColors.GrayText }]);

        _text = new TextBox
        {
            Dock = DockStyle.Fill,
            Multiline = true,
            ReadOnly = true,
            ScrollBars = ScrollBars.Both,
            WordWrap = false,
            Font = new Font("Consolas", 9.5f),
            BackColor = SystemColors.Window,
        };
        Controls.Add(_text);
        Controls.Add(bar);

        _follow.Tick += (_, _) => ReadMore();
        Shown += (_, _) =>
        {
            ReadMore();
            // A finished log opens at its first line; a running one keeps following new lines.
            if (_lines.Any(l => l.StartsWith("# Finished:")))
            {
                _text.SelectionStart = 0;
                _text.ScrollToCaret();
            }
            _follow.Start();
        };
        FormClosed += (_, _) => _follow.Dispose();
    }

    public string LogPath => _path;

    private void ReadMore()
    {
        string added;
        try
        {
            using var stream = new FileStream(_path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            if (stream.Length <= _offset)
                return;
            stream.Seek(_offset, SeekOrigin.Begin);
            using var reader = new StreamReader(stream, Encoding.UTF8);
            added = reader.ReadToEnd();
            _offset = stream.Length;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            if (_lines.Count == 0)
                _text.Text = $"The log can't be read: {ex.Message}";
            return;
        }

        string[] parts = (_partial + added).Split('\n');
        _partial = parts[^1];
        var newLines = parts[..^1].Select(l => l.TrimEnd('\r')).ToList();
        _lines.AddRange(newLines);
        Append(newLines);
    }

    private void Render()
    {
        _text.Clear();
        Append(_lines);
    }

    private void Append(IEnumerable<string> lines)
    {
        var sb = new StringBuilder();
        foreach (string line in lines)
        {
            string? shown = _readable.Checked ? LogFormatter.Format(line) : line;
            if (shown != null)
                sb.Append(shown.Replace("\n", "\r\n")).Append("\r\n");
        }
        if (sb.Length == 0)
            return;
        bool atEnd = _text.SelectionStart >= _text.TextLength - 1;
        _text.AppendText(sb.ToString());
        if (!atEnd)
            return;
        _text.SelectionStart = _text.TextLength;
        _text.ScrollToCaret();
    }
}
