using MediaAnalysis.Tray;

namespace MediaAnalysis.Tray.Tests;

public sealed class CoreEventTests
{
    [Fact]
    public void Progress_percent()
    {
        Assert.Equal(45, CoreEvent.Parse("""{"event": "progress", "stage": "transcribe", "message": "", "completed": 45, "total": 100}""")!.Percent);
        Assert.Null(CoreEvent.Parse("""{"event": "stage_started", "stage": "scan", "message": ""}""")!.Percent);
    }

    [Fact]
    public void Finished_event_tells_whether_the_report_is_generic()
    {
        Assert.True(CoreEvent.Parse("""{"event": "finished", "generic": true}""")!.Generic);
        Assert.False(CoreEvent.Parse("""{"event": "finished", "generic": false}""")!.Generic);
    }

    [Theory]
    [InlineData("")]
    [InlineData("Traceback (most recent call last):")]
    [InlineData("{not json")]
    [InlineData("""{"stage": "scan"}""")]
    public void Non_events_are_ignored(string line)
    {
        Assert.Null(CoreEvent.Parse(line));
    }

    [Fact]
    public void Error_event_keeps_its_message()
    {
        var e = CoreEvent.Parse("""{"event": "error", "exit_code": 1, "message": "Unexpected error: KeyError: 'x'"}""");
        Assert.Equal("error", e!.Event);
        Assert.Equal("Unexpected error: KeyError: 'x'", e.Message);
    }
}
