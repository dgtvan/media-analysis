namespace FootageEditor.Tests;

public sealed class ServiceTests
{
    [Fact]
    public void The_listening_line_gives_the_url()
    {
        Assert.Equal("http://127.0.0.1:5000/?token=abc",
            Service.ListeningUrl("""{"event": "listening", "port": 5000, "url": "http://127.0.0.1:5000/?token=abc"}"""));
        Assert.Null(Service.ListeningUrl("""{"event": "error", "message": "x"}"""));
        Assert.Null(Service.ListeningUrl("Traceback (most recent call last):"));
    }

    [Fact]
    public void The_claude_profile_is_passed_to_the_service()
    {
        var args = Service.Arguments(@"C:\app\ui", new AppSettings { ClaudeProfile = @"C:\Users\me\.claude-work" });
        Assert.Equal(["-m", "media_analysis", "serve", "--ui", @"C:\app\ui", "--claude-profile", @"C:\Users\me\.claude-work"], args);
        Assert.DoesNotContain("--claude-profile", Service.Arguments("ui", new AppSettings()));
    }

    [Fact]
    public void Azure_transcription_is_passed_to_the_service_without_the_key()
    {
        var settings = new AppSettings { Transcriber = AppSettings.AzureTranscriber, AzureEndpoint = "https://r.cognitiveservices.azure.com/", AzureKey = "secret" };
        Assert.Equal(["-m", "media_analysis", "serve", "--ui", "ui", "--transcriber", "azure-speech", "--azure-endpoint", "https://r.cognitiveservices.azure.com/"],
            Service.Arguments("ui", settings));
        Assert.Equal("secret", settings.Copy().AzureKey);
        Assert.DoesNotContain("secret", System.Text.Json.JsonSerializer.Serialize(settings));
        settings.Transcriber = null;
        Assert.DoesNotContain("--transcriber", Service.Arguments("ui", settings));
    }

    [Theory]
    [InlineData(new[] { "--data-dir", @"C:\x\data" }, @"C:\x\data")]
    [InlineData(new[] { "--data-dir" }, null)]
    [InlineData(new string[0], null)]
    public void Data_dir_argument_is_required(string[] args, string? expected) =>
        Assert.Equal(expected, Program.DataDirArgument(args));

    [Fact]
    public void Core_config_reports_a_missing_engine()
    {
        string dir = Directory.CreateTempSubdirectory("fe-core-").FullName;
        try
        {
            File.WriteAllText(Path.Combine(dir, "core.json"), """{"Python": "C:\\nowhere\\python.exe", "PythonPath": "core"}""");
            var ex = Assert.Throws<InvalidOperationException>(() => CoreConfig.Load(dir));
            Assert.Contains("missing", ex.Message);
        }
        finally
        {
            Directory.Delete(dir, true);
        }
    }
}
