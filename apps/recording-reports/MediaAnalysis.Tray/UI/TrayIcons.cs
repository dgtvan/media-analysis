using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;

namespace MediaAnalysis.Tray;

/// <summary>
/// Tray icons drawn at startup so the app ships without image assets.
/// </summary>
static class TrayIcons
{
    private static readonly Color WarningFill = Color.FromArgb(255, 196, 0);
    private static readonly Color IdleColor = Color.FromArgb(150, 150, 150);
    private static readonly Color WorkingColor = Color.FromArgb(30, 120, 230);
    private static readonly Color WaitingColor = Color.FromArgb(245, 150, 0);

    private static readonly float[] StillHeights = [0.35f, 0.65f, 1f, 0.55f, 0.8f];

    /// <summary>Frames of the working animation; <see cref="FrameInterval"/> apart, one wave passes per cycle.</summary>
    public const int FrameCount = 12;
    public static readonly TimeSpan FrameInterval = TimeSpan.FromMilliseconds(110);

    // Sound-wave bars; the colour gives the state.
    public static Icon Idle { get; } = Create(g => DrawWave(g, IdleColor, StillHeights));
    public static Icon Waiting { get; } = Create(g => DrawWave(g, WaitingColor, StillHeights));

    /// <summary>Working: the bars rise and fall in a wave that travels right to left.</summary>
    public static Icon[] Working { get; } = Frames(heights => g => DrawWave(g, WorkingColor, heights));

    /// <summary>Needs attention while idle: a large warning triangle.</summary>
    public static Icon Warning { get; } = Create(g => DrawTriangle(g, new RectangleF(0, 0, Size, Size)));

    /// <summary>Needs attention while analysing: the moving working wave with a warning badge.</summary>
    public static Icon[] WorkingWarning { get; } = Frames(heights => g => DrawWaveWithBadge(g, WorkingColor, heights));

    /// <summary>Needs attention while projects wait: the waiting wave with a warning badge.</summary>
    public static Icon WaitingWarning { get; } = Create(g => DrawWaveWithBadge(g, WaitingColor, StillHeights));

    /// <summary>
    /// Bar heights (share of the icon) for one animation frame: a sine over the bars whose phase advances with the frame,
    /// so the crests move towards the first bar, right to left.
    /// </summary>
    internal static float[] WaveHeights(int frame, int bars = 5)
    {
        var heights = new float[bars];
        for (int i = 0; i < bars; i++)
        {
            double v = 0.5 + 0.5 * Math.Sin(2 * Math.PI * (i * 1.2 / bars + (double)frame / FrameCount));
            heights[i] = (float)(0.25 + 0.75 * v);
        }
        return heights;
    }

    private static Icon[] Frames(Func<float[], Action<Graphics>> draw) =>
        Enumerable.Range(0, FrameCount).Select(f => Create(draw(WaveHeights(f)))).ToArray();

    private static int Size => SystemInformation.SmallIconSize.Width;

    private static void DrawWaveWithBadge(Graphics g, Color color, float[] heights)
    {
        DrawWave(g, color, heights);
        float badge = Size * 0.62f;
        DrawTriangle(g, new RectangleF(Size - badge, Size - badge, badge, badge));
    }

    private static void DrawWave(Graphics g, Color color, float[] heights)
    {
        int size = Size;
        // Whole-pixel bars and gaps: at 16 px anything in between blurs into a smudge.
        int bar = Math.Max(2, size / 7);
        int gap = Math.Max(1, bar / 2);
        int left = (size - (heights.Length * bar + (heights.Length - 1) * gap)) / 2;
        using var brush = new SolidBrush(color);
        var offset = g.PixelOffsetMode;
        g.PixelOffsetMode = PixelOffsetMode.Half;
        for (int i = 0; i < heights.Length; i++)
        {
            int height = Math.Max(2, (int)Math.Round(size * heights[i] / 2) * 2);
            g.FillRectangle(brush, left + i * (bar + gap), (size - height) / 2, bar, height);
        }
        g.PixelOffsetMode = offset;
    }

    private static void DrawTriangle(Graphics g, RectangleF area)
    {
        var points = new[]
        {
            new PointF(area.Left + area.Width / 2, area.Top + 0.5f),
            new PointF(area.Right - 0.5f, area.Bottom - 0.5f),
            new PointF(area.Left + 0.5f, area.Bottom - 0.5f),
        };
        using (var fill = new SolidBrush(WarningFill))
            g.FillPolygon(fill, points);
        using (var outline = new Pen(Color.Black, Math.Max(1f, area.Width / 16f)))
            g.DrawPolygon(outline, points);

        float barWidth = Math.Max(1.5f, area.Width / 8f);
        float centerX = area.Left + area.Width / 2;
        using var mark = new SolidBrush(Color.Black);
        g.FillRectangle(mark, centerX - barWidth / 2, area.Top + area.Height * 0.32f, barWidth, area.Height * 0.36f);
        g.FillRectangle(mark, centerX - barWidth / 2, area.Top + area.Height * 0.75f, barWidth, barWidth);
    }

    private static Icon Create(Action<Graphics> draw)
    {
        using var bitmap = new Bitmap(Size, Size);
        using (var g = Graphics.FromImage(bitmap))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            draw(g);
        }

        IntPtr handle = bitmap.GetHicon();
        try
        {
            return (Icon)Icon.FromHandle(handle).Clone();
        }
        finally
        {
            DestroyIcon(handle);
        }
    }

    [DllImport("user32.dll")]
    private static extern bool DestroyIcon(IntPtr handle);
}
