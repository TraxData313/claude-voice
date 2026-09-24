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
    /// </code>
    /// </summary>
    internal sealed class Options
    {
        public string For, Engine, Folder, Source;

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
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            var form = new SetupForm(Options.Parse(args));
            Application.Run(form);
            Environment.ExitCode = form.Failed ? 1 : 0;
        }
    }
}
