using System;
using System.Net;
using System.Windows.Forms;

namespace ClaudeVoiceSetup
{
    /// <summary>
    /// What a caller may say about the install, all optional:
    /// <code>
    ///   --for "Immersive AI"     who sent them here; the pages speak to that
    ///   --engine qwen            pre-pick an engine (still only if this machine runs it)
    ///   --dir C:\somewhere       where to install
    ///   --source &lt;zip url | folder&gt;   where the code comes from (a folder is how this is tested)
    ///   --data C:\somewhere      where the big voice files go (Studio, the Qwen model, Breeze)
    ///   --quiet                  no window at all: a game shows the progress, read from the status file
    /// </code>
    /// </summary>
    internal sealed class Options
    {
        public string For, Engine, Folder, Source, Data;

        /// <summary>No window: install at once and report only through the status file.</summary>
        public bool Quiet;

        /// <summary>Opens straight on one page -- for screenshots of it, never for use.</summary>
        public string Show, Shot;

        /// <summary>Installs at once with no clicks and exits 0 or 1 -- for testing the whole road.</summary>
        public bool Auto;

        public static Options Parse(string[] args)
        {
            var o = new Options();
            for (var i = 0; i < args.Length; i++)
            {
                var next = i + 1 < args.Length ? args[i + 1] : null;
                switch (args[i].ToLowerInvariant())
                {
                    case "--for": o.For = next; i++; break;
                    case "--engine": o.Engine = next; i++; break;
                    case "--dir": o.Folder = next; i++; break;
                    case "--source": o.Source = next; i++; break;
                    case "--show": o.Show = next; i++; break;
                    case "--shot": o.Shot = next; i++; break;
                    case "--auto": o.Auto = true; break;
                    case "--quiet": o.Quiet = true; break;
                    case "--data": o.Data = next; i++; break;
                }
            }
            return o;
        }
    }

    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            // GitHub refuses anything older, and .NET Framework does not always offer it unasked.
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
            var options = Options.Parse(args);
            if (options.Quiet)
            {
                Environment.ExitCode = InstallQuietly(options);
                return;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            var form = new SetupForm(options);
            Application.Run(form);
            Environment.ExitCode = form.Failed ? 1 : 0;
        }

        /// <summary>
        /// The game's road: everything decided before this starts (the engine, where the files go),
        /// nothing on screen, and every step written to the status file the game reads. Exit codes:
        /// 0 installed, 1 failed, 2 another install is already running, 3 stopped on request.
        /// </summary>
        private static int InstallQuietly(Options o)
        {
            if (StatusFile.RunningPid() != null) return 2;
            var folder = o.Folder ?? Installer.ExistingInstall() ?? Installer.DefaultFolder;
            var engine = EngineInfo.ById(o.Engine ?? "")
                         ?? Machine.Recommend(Machine.DetectNvidia(), Machine.FreeGb(o.Data ?? folder), true);
            var plan = new InstallPlan
            {
                Engine = engine,
                Folder = folder,
                DataDir = o.Data ?? "",
                DesktopIcon = false,
                ForClaudeCode = false,
                Quiet = true,
                Source = o.Source ?? Installer.DefaultSource,
            };
            try
            {
                new Installer().RunAsync(plan, System.Threading.CancellationToken.None).GetAwaiter().GetResult();
                return 0;
            }
            catch (OperationCanceledException) { return 3; }
            catch { return 1; }
        }
    }
}
