using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json.Serialization;

namespace RecordingReports.Analysis;

/// <summary>How much of the computer an analysis may use. Saved by name in settings.json and pending.json.</summary>
[JsonConverter(typeof(JsonStringEnumConverter<ResourceLevel>))]
enum ResourceLevel
{
    Light,
    Balanced,
    Fast,
    Full,
}

static class ResourceLevels
{
    public const ResourceLevel Default = ResourceLevel.Light;

    public static readonly ResourceLevel[] All = Enum.GetValues<ResourceLevel>();

    /// <summary>Share of the whole CPU (all cores together), in percent; null = no limit.</summary>
    public static int? CpuPercent(this ResourceLevel level) => level switch
    {
        ResourceLevel.Light => 25,
        ResourceLevel.Balanced => 50,
        ResourceLevel.Fast => 75,
        _ => null,
    };

    public static ProcessPriorityClass Priority(this ResourceLevel level) => level switch
    {
        ResourceLevel.Light => ProcessPriorityClass.Idle,
        ResourceLevel.Full => ProcessPriorityClass.Normal,
        _ => ProcessPriorityClass.BelowNormal,
    };

    /// <summary>E.g. "Balanced (up to 50% of the CPU, below-normal priority)".</summary>
    public static string Describe(this ResourceLevel level) => $"{level} ({Details(level)})";

    public static string Details(this ResourceLevel level) =>
        (level.CpuPercent() is { } percent ? $"up to {percent}% of the CPU" : "no limit") + ", " + level.Priority() switch
        {
            ProcessPriorityClass.Idle => "lowest priority",
            ProcessPriorityClass.BelowNormal => "below-normal priority",
            _ => "normal priority",
        };
}

/// <summary>
/// A Windows job object holding the core and every process it starts (ffmpeg, Claude Code), so one CPU cap and
/// priority covers them all. Limits can change while the analysis runs.
/// </summary>
sealed class ResourceJob : IDisposable
{
    private readonly SafeJobHandle _handle;

    public ResourceJob()
    {
        _handle = CreateJobObject(IntPtr.Zero, null);
        if (_handle.IsInvalid)
            throw new Win32Exception();
    }

    public ResourceLevel Level { get; private set; } = ResourceLevel.Full;

    /// <summary>Adds a started process. Processes it started before this call stay outside the job.</summary>
    public void Add(Process process)
    {
        if (!AssignProcessToJobObject(_handle, process.Handle))
            throw new Win32Exception();
    }

    public void Apply(ResourceLevel level)
    {
        var basic = new BasicLimits { LimitFlags = LimitPriorityClass, PriorityClass = (uint)level.Priority() };
        Set(JobObjectBasicLimitInformation, ref basic);
        var rate = level.CpuPercent() is { } percent
            ? new CpuRateControl { ControlFlags = CpuRateEnable | CpuRateHardCap, CpuRate = (uint)(percent * 100) }
            : new CpuRateControl();
        Set(JobObjectCpuRateControlInformation, ref rate);
        Level = level;
    }

    /// <summary>The CPU cap in force, in percent (null = none), read back from Windows.</summary>
    internal int? CpuPercentInForce()
    {
        var rate = new CpuRateControl();
        if (!QueryInformationJobObject(_handle, JobObjectCpuRateControlInformation, ref rate, Marshal.SizeOf<CpuRateControl>(), IntPtr.Zero))
            throw new Win32Exception();
        return (rate.ControlFlags & CpuRateEnable) != 0 ? (int)(rate.CpuRate / 100) : null;
    }

    private void Set(int infoClass, ref BasicLimits info)
    {
        if (!SetInformationJobObject(_handle, infoClass, ref info, Marshal.SizeOf<BasicLimits>()))
            throw new Win32Exception();
    }

    private void Set(int infoClass, ref CpuRateControl info)
    {
        if (!SetInformationJobObject(_handle, infoClass, ref info, Marshal.SizeOf<CpuRateControl>()))
            throw new Win32Exception();
    }

    public void Dispose() => _handle.Dispose();

    private const int JobObjectBasicLimitInformation = 2;
    private const int JobObjectCpuRateControlInformation = 15;
    private const uint LimitPriorityClass = 0x20;
    private const uint CpuRateEnable = 0x1;
    private const uint CpuRateHardCap = 0x4;

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimits
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public nuint MinimumWorkingSetSize;
        public nuint MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public nuint Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct CpuRateControl
    {
        public uint ControlFlags;
        public uint CpuRate;
    }

    private sealed class SafeJobHandle() : Microsoft.Win32.SafeHandles.SafeHandleZeroOrMinusOneIsInvalid(true)
    {
        protected override bool ReleaseHandle() => CloseHandle(handle);
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern SafeJobHandle CreateJobObject(IntPtr attributes, string? name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(SafeJobHandle job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(SafeJobHandle job, int infoClass, ref BasicLimits info, int length);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(SafeJobHandle job, int infoClass, ref CpuRateControl info, int length);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool QueryInformationJobObject(SafeJobHandle job, int infoClass, ref CpuRateControl info, int length, IntPtr returnLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);
}
