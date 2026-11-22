using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("NetCraft")]
[assembly: AssemblyProduct("NetCraft Network Diagnostics Studio")]

internal static class Launcher
{
    [STAThread]
    private static int Main(string[] args)
    {
        var root = AppDomain.CurrentDomain.BaseDirectory;
        var arguments = new StringBuilder("-I -B ");
        arguments.Append(Quote(Path.Combine(root, "portable_entry.py")));
        foreach (var argument in args)
            arguments.Append(' ').Append(Quote(argument));

        var launch = new ProcessStartInfo
        {
            FileName = Path.Combine(root, "runtime", "pythonw.exe"),
            Arguments = arguments.ToString(),
            UseShellExecute = false,
            CreateNoWindow = true
        };
        // Resolve Qt plugins from the shipped PySide6 package.
        foreach (var name in new[]
        {
            "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM",
            "QT_QPA_PLATFORMTHEME", "QT_STYLE_OVERRIDE", "PYSIDE_DISABLE_INTERNAL_QT_CONF"
        })
            launch.EnvironmentVariables.Remove(name);

        try
        {
            using (var process = Process.Start(launch))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Win32Exception)
        {
            MessageBox.Show(
                "NetCraft could not start its bundled Python runtime. Extract the whole ZIP "
                + "and keep NetCraft.exe beside the runtime and app folders.",
                "NetCraft", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    // Quote a single argument for Windows' backslash-and-quote command-line parsing.
    private static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        var backslashes = 0;
        foreach (var character in value)
        {
            if (character == '\\')
            {
                backslashes++;
                continue;
            }
            if (character == '"')
                result.Append('\\', 2 * backslashes + 1);
            else
                result.Append('\\', backslashes);
            result.Append(character);
            backslashes = 0;
        }
        return result.Append('\\', 2 * backslashes).Append('"').ToString();
    }
}
