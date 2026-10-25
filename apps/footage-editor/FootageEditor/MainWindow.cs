using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace FootageEditor;

/// <summary>
/// The window: hosts the presentation (HTML served by the core's local service) in WebView2 and does only what a web
/// page can't: start and stop the service, pick and drop files with their paths, the settings dialog, and close on
/// request of an install (App deployment standard 3.5: refused while an analysis or a chat turn runs).
/// </summary>
sealed class MainWindow : Form
{
    readonly string _dataDir;
    readonly WebView2 _web = new() { Dock = DockStyle.Fill };
    readonly Label _status = new() { Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter, ForeColor = Color.Gainsboro };
    readonly EventWaitHandle _exitRequest = new(false, EventResetMode.AutoReset, $@"Local\{Program.AppId}-Exit");
    readonly EventWaitHandle _exitRefused = new(false, EventResetMode.AutoReset, $@"Local\{Program.AppId}-ExitRefused");
    readonly EventWaitHandle _show = new(false, EventResetMode.AutoReset, $@"Local\{Program.AppId}-Show");
    AppSettings _settings;
    Service? _service;
    bool _closingConfirmed;

    public MainWindow(string dataDir)
    {
        _dataDir = dataDir;
        _settings = AppSettings.Load(dataDir);
        Text = Program.AppName;
        Width = 1600;
        Height = 950;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(21, 23, 28);
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        _status.Text = "Starting…";
        Controls.Add(_status);
        Load += async (_, _) => await StartAsync();
        WatchHandle(_exitRequest, () => _ = OnExitRequestAsync());
        WatchHandle(_show, () => { if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal; Activate(); });
    }

    void WatchHandle(WaitHandle handle, Action action) =>
        ThreadPool.RegisterWaitForSingleObject(handle, (_, _) => { if (IsHandleCreated) BeginInvoke(action); }, null, -1, false);

    async Task StartAsync()
    {
        try
        {
            string appDir = AppContext.BaseDirectory;
            var core = CoreConfig.Load(appDir);
            _service = await Task.Run(() => Service.Start(core, Path.Combine(appDir, "ui"), _dataDir, _settings));
            if (!Controls.Contains(_web))
            {
                var env = await CoreWebView2Environment.CreateAsync(userDataFolder: Path.Combine(_dataDir, "webview2"));
                await _web.EnsureCoreWebView2Async(env);
                _web.CoreWebView2.WebMessageReceived += OnWebMessage;
                _web.CoreWebView2.Settings.AreDevToolsEnabled = false;
                _web.CoreWebView2.Settings.IsStatusBarEnabled = false;
                // The page has its own timeline zoom; browser zoom (Ctrl + wheel, Ctrl +/-, pinch) only breaks the layout.
                _web.CoreWebView2.Settings.IsZoomControlEnabled = false;
                _web.CoreWebView2.Settings.IsPinchZoomEnabled = false;
                _web.ZoomFactor = 1;
                Controls.Add(_web);
                _web.BringToFront();
            }
            _web.CoreWebView2.Navigate(_service.Url);
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException or WebView2RuntimeNotFoundException)
        {
            _status.Text = ex is WebView2RuntimeNotFoundException
                ? "The Microsoft Edge WebView2 Runtime is missing. Install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703 and start again."
                : ex.Message;
            _status.BringToFront();
        }
    }

    void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        string? type;
        try
        {
            using var doc = JsonDocument.Parse(e.WebMessageAsJson);
            type = doc.RootElement.TryGetProperty("type", out var t) ? t.GetString() : null;
        }
        catch (JsonException)
        {
            return;
        }
        switch (type)
        {
            case "pickFiles":
                using (var dialog = new OpenFileDialog
                {
                    Multiselect = true,
                    Title = "Import footage",
                    Filter = "Video and audio|*.mp4;*.mov;*.mkv;*.avi;*.webm;*.m4v;*.wmv;*.mts;*.ts;*.mpg;*.mpeg;*.mp3;*.wav;*.m4a;*.aac;*.flac;*.ogg;*.opus|All files|*.*",
                })
                {
                    if (dialog.ShowDialog(this) == DialogResult.OK)
                        SendFiles(dialog.FileNames);
                }
                break;
            case "dropped":
                var paths = e.AdditionalObjects?.OfType<CoreWebView2File>().Select(f => f.Path).ToArray() ?? [];
                SendFiles(paths);
                break;
            case "settings":
                ShowSettings();
                break;
        }
    }

    void SendFiles(string[] paths) =>
        _web.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(new { type = "files", paths }));

    void ShowSettings()
    {
        using var dialog = new SettingsDialog(_settings.ClaudeProfile, AppSettings.ClaudeProfiles());
        if (dialog.ShowDialog(this) != DialogResult.OK || dialog.ClaudeProfile == _settings.ClaudeProfile)
            return;
        _settings.ClaudeProfile = dialog.ClaudeProfile;
        _settings.Save();
        _service?.Dispose();
        _ = StartAsync();
    }

    async Task OnExitRequestAsync()
    {
        if (_service != null && await _service.IsBusyAsync())
        {
            _exitRefused.Set();
            return;
        }
        _closingConfirmed = true;
        Close();
    }

    protected override async void OnFormClosing(FormClosingEventArgs e)
    {
        if (!_closingConfirmed && _service != null && e.CloseReason == CloseReason.UserClosing)
        {
            e.Cancel = true;
            bool busy = await _service.IsBusyAsync();
            if (!busy || MessageBox.Show(this,
                    "An analysis or a chat answer is still running. Close anyway?\n\nIt stops now; analysing again continues where it stopped.",
                    Program.AppName, MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes)
            {
                _closingConfirmed = true;
                Close();
            }
            return;
        }
        base.OnFormClosing(e);
    }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        _service?.Dispose();
        _exitRequest.Dispose();
        _exitRefused.Dispose();
        _show.Dispose();
        base.OnFormClosed(e);
    }
}

/// <summary>Which Claude Code login the analyses and the chat use.</summary>
sealed class SettingsDialog : Form
{
    readonly ComboBox _profiles = new() { DropDownStyle = ComboBoxStyle.DropDown, Width = 420 };

    public string? ClaudeProfile => string.IsNullOrWhiteSpace(_profiles.Text) ? null : _profiles.Text.Trim();

    public SettingsDialog(string? current, List<string> found)
    {
        Text = $"{Program.AppName} settings";
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = MinimizeBox = false;
        StartPosition = FormStartPosition.CenterParent;
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        Padding = new Padding(14);
        _profiles.Items.AddRange([.. found]);
        _profiles.Text = current ?? "";
        var ok = new Button { Text = "Save", DialogResult = DialogResult.OK, AutoSize = true };
        var cancel = new Button { Text = "Cancel", DialogResult = DialogResult.Cancel, AutoSize = true };
        AcceptButton = ok;
        CancelButton = cancel;
        var layout = new TableLayoutPanel { AutoSize = true, ColumnCount = 1 };
        layout.Controls.Add(new Label { Text = "Claude profile (the Claude Code config folder whose login is used; empty = default):", AutoSize = true });
        layout.Controls.Add(_profiles);
        var buttons = new FlowLayoutPanel { AutoSize = true, FlowDirection = FlowDirection.RightToLeft, Dock = DockStyle.Fill };
        buttons.Controls.AddRange([cancel, ok]);
        layout.Controls.Add(buttons);
        Controls.Add(layout);
    }
}
