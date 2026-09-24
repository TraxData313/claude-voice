using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace ClaudeVoiceSetup
{
    internal sealed class InstallPlan
    {
        public EngineInfo Engine;
        public string Folder = "";
        public bool DesktopIcon = true;
        public bool ForClaudeCode;
        public string Source = Installer.DefaultSource;
    }

    /// <summary>
    /// Puts claude-voice on the machine: the code, then whatever the chosen engine needs, then the
    /// engine running and saying hello.
    /// <para>
    /// It installs NOTHING itself beyond copying files. Every real step -- Python, Studio, the
    /// models, pocket-tts, Breeze's environment -- is setup.ps1's, run exactly as a person would run
    /// it, so this window and the command line can never disagree about what an install is. That
    /// script resumes where it stopped, which is what makes "Try again" an honest button.
    /// </para>
    /// </summary>
    internal sealed class Installer
    {
        public const string DefaultSource = "https://github.com/TraxData313/claude-voice/archive/refs/heads/main.zip";
        public const int Port = 8765;

        /// <summary>A headline for a person, and how far along, 0..1, or null for "working on it".</summary>
        public event Action<string, double?, string> Stage;
        public event Action<string> Line;

        private readonly StringBuilder _log = new StringBuilder();
        public string Log { get { lock (_log) return _log.ToString(); } }

        public static string DefaultFolder =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "claude-voice");

        public static string WherePath =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "claude-voice", "where.json");

        /// <summary>A copy already on this machine, from the note every running engine leaves. Null if none.</summary>
        public static string ExistingInstall()
        {
            try
            {
                if (!File.Exists(WherePath)) return null;
                var doc = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(WherePath));
                var root = doc != null && doc.TryGetValue("root", out var r) ? r as string : null;
                return root != null && File.Exists(Path.Combine(root, "voice_lib.py")) ? root : null;
            }
            catch { return null; }
        }

        public async Task RunAsync(InstallPlan plan, CancellationToken cancel)
        {
            Say($"Installing claude-voice into {plan.Folder}, engine {plan.Engine.Id}.");
            Directory.CreateDirectory(plan.Folder);

            // --- the code ---------------------------------------------------------------------
            var gitCheckout = Directory.Exists(Path.Combine(plan.Folder, ".git"));
            if (gitCheckout)
            {
                // Somebody's working copy. Its code is theirs and is updated by git, never by a zip
                // laid over the top of their changes.
                Say("That folder is a git checkout, so its code is left as it is.");
            }
            else
            {
                Report("Fetching claude-voice itself…", 0.02, "");
                await StopRunningEngineAsync();
                await FetchCodeAsync(plan, cancel);
            }

            // --- the engine -------------------------------------------------------------------
            cancel.ThrowIfCancellationRequested();
            Report(FirstHeadline(plan.Engine), null, "");
            await RunSetupAsync(plan, cancel);

            // --- hello ------------------------------------------------------------------------
            Report("Waking the voice up…", null, "The first start loads the model — up to a minute.");
            if (!await WaitForEngineAsync(TimeSpan.FromMinutes(4), cancel))
                throw new InstallException("The voice was installed, but it did not start. Double-click the Abby icon on your desktop, or run this setup again.");
            await SayHelloAsync();
            Report("Done.", 1, "");
        }

        // ---------------------------------------------------------------------------------------

        private async Task FetchCodeAsync(InstallPlan plan, CancellationToken cancel)
        {
            var temp = Path.Combine(Path.GetTempPath(), "claude-voice-setup-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            Directory.CreateDirectory(temp);
            try
            {
                string source;
                if (Directory.Exists(plan.Source))
                {
                    source = plan.Source;             // a local copy -- how this window is tested
                    Say($"Copying from {source}.");
                }
                else
                {
                    var zip = Path.Combine(temp, "claude-voice.zip");
                    await DownloadAsync(plan.Source, zip, "claude-voice", cancel);
                    var unpacked = Path.Combine(temp, "unpacked");
                    ZipFile.ExtractToDirectory(zip, unpacked);
                    // GitHub wraps the tree in one folder named after the branch.
                    var inner = Directory.GetDirectories(unpacked);
                    source = inner.Length == 1 && !File.Exists(Path.Combine(unpacked, "voice_lib.py")) ? inner[0] : unpacked;
                }

                if (!File.Exists(Path.Combine(source, "setup.ps1")))
                    throw new InstallException("The download did not hold claude-voice. Check your internet connection and try again.");

                CopyTree(source, plan.Folder);
                Say("claude-voice is in place.");
            }
            finally
            {
                try { Directory.Delete(temp, true); } catch { /* the temp folder is Windows' to sweep */ }
            }
        }

        /// <summary>Everything except what belongs to this machine: its settings, its logs, a git
        /// history, and the setup program's own build output.</summary>
        private static readonly HashSet<string> NeverCopied = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            { ".git", "logs", "__pycache__", "config.json", "bin", "obj", "dist", "voices-local", "installed.json" };

        private static void CopyTree(string from, string to)
        {
            Directory.CreateDirectory(to);
            foreach (var file in Directory.GetFiles(from))
            {
                if (NeverCopied.Contains(Path.GetFileName(file))) continue;
                File.Copy(file, Path.Combine(to, Path.GetFileName(file)), true);
            }
            foreach (var dir in Directory.GetDirectories(from))
            {
                if (NeverCopied.Contains(Path.GetFileName(dir))) continue;
                CopyTree(dir, Path.Combine(to, Path.GetFileName(dir)));
            }
        }

        private async Task DownloadAsync(string url, string dest, string what, CancellationToken cancel)
        {
            var req = (HttpWebRequest)WebRequest.Create(url);
            req.UserAgent = "claude-voice-setup";
            using (var res = (HttpWebResponse)await req.GetResponseAsync())
            using (var input = res.GetResponseStream())
            using (var output = File.Create(dest))
            {
                var total = res.ContentLength;
                var buf = new byte[1 << 16];
                long done = 0;
                int n;
                var tick = Stopwatch.StartNew();
                while ((n = await input.ReadAsync(buf, 0, buf.Length, cancel)) > 0)
                {
                    await output.WriteAsync(buf, 0, n, cancel);
                    done += n;
                    if (tick.ElapsedMilliseconds > 300)
                    {
                        tick.Restart();
                        Report("Fetching claude-voice itself…", total > 0 ? 0.02 + 0.03 * done / total : (double?)null,
                               Megabytes(done, total));
                    }
                }
            }
            Say($"Fetched {what}.");
        }

        // ---------------------------------------------------------------------------------------

        private Process _setup;

        private async Task RunSetupAsync(InstallPlan plan, CancellationToken cancel)
        {
            var script = Path.Combine(plan.Folder, "setup.ps1");
            var args = new StringBuilder();
            args.Append("-NoProfile -ExecutionPolicy Bypass -File ").Append(Quote(script));
            args.Append(" -Engine ").Append(plan.Engine.Id);
            args.Append(" -NoPanel");
            if (!plan.DesktopIcon) args.Append(" -NoShortcut");
            if (plan.ForClaudeCode)
                args.Append(" -ProjectDir ").Append(Quote(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile)));
            else
                args.Append(" -NoClaude");

            Say("> powershell " + args);
            var psi = new ProcessStartInfo("powershell.exe", args.ToString())
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                WorkingDirectory = plan.Folder,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };
            psi.EnvironmentVariables["CLAUDE_VOICE_PROGRESS"] = "1";

            var exited = new TaskCompletionSource<int>();
            _setup = new Process { StartInfo = psi, EnableRaisingEvents = true };
            _setup.OutputDataReceived += (_, e) => { if (e.Data != null) Hear(e.Data, plan.Engine); };
            _setup.ErrorDataReceived += (_, e) => { if (e.Data != null) Say(e.Data); };
            _setup.Exited += (_, __) => exited.TrySetResult(_setup.ExitCode);
            _setup.Start();
            _setup.BeginOutputReadLine();
            _setup.BeginErrorReadLine();

            using (cancel.Register(() => { try { KillTree(_setup.Id); } catch { } }))
            {
                var code = await exited.Task;
                cancel.ThrowIfCancellationRequested();
                if (code != 0)
                    throw new InstallException(LastProblem() ?? $"The install stopped (code {code}).");
            }
        }

        private static readonly Regex Progress = new Regex(@"^@progress (.+)\|(\d+)\|(\d+)$");
        private static readonly Regex BreezeStep = new Regex(@"^\[(\d+)/(\d+)\]\s*(.+)$");

        /// <summary>Turns setup.ps1's own lines into something a person wants to read.</summary>
        private void Hear(string line, EngineInfo engine)
        {
            var text = line.TrimEnd();
            var p = Progress.Match(text);
            if (p.Success)
            {
                var label = p.Groups[1].Value.Trim();
                var done = long.Parse(p.Groups[2].Value, CultureInfo.InvariantCulture);
                var total = long.Parse(p.Groups[3].Value, CultureInfo.InvariantCulture);
                Report(FriendlyDownload(label), total > 0 ? 0.05 + 0.8 * done / total : (double?)null, Megabytes(done, total));
                return;
            }

            Say(text);
            var b = BreezeStep.Match(text.Trim());
            if (b.Success)
            {
                var i = int.Parse(b.Groups[1].Value, CultureInfo.InvariantCulture);
                var n = int.Parse(b.Groups[2].Value, CultureInfo.InvariantCulture);
                Report("Breeze: " + b.Groups[3].Value.Trim(), 0.1 + 0.85 * (i - 1) / Math.Max(1, n),
                       i == 1 ? "This is the long part — about 11 GB. It carries on if the connection drops." : $"Step {i} of {n}");
                return;
            }

            var lower = text.ToLowerInvariant();
            if (lower.StartsWith("python") && lower.Contains("installing")) Report("Installing Python (just for this)…", null, "");
            else if (lower.StartsWith("pocket-tts") && lower.Contains("installing")) Report("Installing the Pocket voice…", null, "A few hundred MB — no graphics card needed.");
            else if (lower.StartsWith("studio") && lower.Contains("unpack")) Report("Unpacking the speech engine…", null, "");
            else if (lower.StartsWith("starting the engine") || lower.StartsWith("loading the talker model"))
                Report("Waking the voice up…", null, "The first start loads the model — up to a minute.");
        }

        private static string FirstHeadline(EngineInfo engine) =>
            engine.Id == "breeze" ? "Getting Breeze ready…" :
            engine.Id == "pocket" ? "Getting Pocket ready…" : "Getting Qwen ready…";

        private static string FriendlyDownload(string label)
        {
            var l = label.ToLowerInvariant();
            if (l.Contains("talker")) return "Downloading the voice model…";
            if (l.Contains("tokenizer")) return "Downloading the sound decoder…";
            if (l.Contains("python")) return "Downloading Python…";
            if (l.Contains("studio") || l.Contains(".msi") || l.Contains(".zip")) return "Downloading the speech engine…";
            return "Downloading " + label + "…";
        }

        private static string Megabytes(long done, long total) =>
            total > 0
                ? string.Format(CultureInfo.InvariantCulture, "{0:0.0} of {1:0.0} GB", done / 1073741824.0, total / 1073741824.0)
                : string.Format(CultureInfo.InvariantCulture, "{0:0} MB", done / 1048576.0);

        /// <summary>The last line that reads like the reason it stopped -- what a person should see
        /// first, above the whole log.</summary>
        private string LastProblem()
        {
            var lines = Log.Split('\n');
            for (var i = lines.Length - 1; i >= 0; i--)
            {
                var t = lines[i].Trim();
                if (t.Length == 0 || t.StartsWith(">")) continue;
                if (Regex.IsMatch(t, @"(?i)\b(error|exception|failed|stopped|cannot|could not|not found|exited with)\b"))
                    return t.Length > 300 ? t.Substring(0, 300) + "…" : t;
            }
            return null;
        }

        // ---------------------------------------------------------------------------------------

        public static async Task<bool> EngineUpAsync()
        {
            try
            {
                var json = await PostAsync("/health", "{}", TimeSpan.FromSeconds(2));
                return json != null;
            }
            catch { return false; }
        }

        private static async Task StopRunningEngineAsync()
        {
            // New code under a running engine would go on being the OLD engine until it restarts,
            // and the next thing a game asks of it would be answered by a version that does not
            // know the question.
            if (!await EngineUpAsync()) return;
            try { await PostAsync("/quit", "{}", TimeSpan.FromSeconds(3)); } catch { }
            await Task.Delay(1500);
        }

        private static async Task<bool> WaitForEngineAsync(TimeSpan patience, CancellationToken cancel)
        {
            var until = DateTime.UtcNow + patience;
            while (DateTime.UtcNow < until)
            {
                cancel.ThrowIfCancellationRequested();
                try
                {
                    var json = await PostAsync("/health", "{}", TimeSpan.FromSeconds(2));
                    if (json != null)
                    {
                        var doc = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
                        if (doc != null && doc.TryGetValue("ready", out var ready) && ready is bool b && b) return true;
                    }
                }
                catch { /* not up yet */ }
                await Task.Delay(1500, cancel);
            }
            return false;
        }

        public const string HelloLine = "Hi! I'm all set up, and I can't wait to talk with you.";

        public static async Task SayHelloAsync(string text = HelloLine)
        {
            try
            {
                var body = new JavaScriptSerializer().Serialize(new Dictionary<string, object>
                {
                    { "text", text }, { "announce", false }, { "project", "setup" },
                });
                await PostAsync("/speak", body, TimeSpan.FromSeconds(5));
            }
            catch { /* a hello nobody heard is not a failed install */ }
        }

        public static async Task OpenPanelAsync()
        {
            try { await PostAsync("/panel", "{}", TimeSpan.FromSeconds(4)); } catch { }
        }

        private static async Task<string> PostAsync(string route, string json, TimeSpan timeout)
        {
            var req = (HttpWebRequest)WebRequest.Create($"http://127.0.0.1:{Port}{route}");
            req.Method = "POST";
            req.ContentType = "application/json";
            req.Timeout = (int)timeout.TotalMilliseconds;
            req.Proxy = null;             // localhost must never go through a corporate proxy
            var bytes = Encoding.UTF8.GetBytes(json);
            using (var s = await req.GetRequestStreamAsync()) await s.WriteAsync(bytes, 0, bytes.Length);
            try
            {
                using (var res = (HttpWebResponse)await req.GetResponseAsync())
                using (var reader = new StreamReader(res.GetResponseStream(), Encoding.UTF8))
                    return await reader.ReadToEndAsync();
            }
            catch (WebException ex) when (ex.Response is HttpWebResponse)
            {
                using (var reader = new StreamReader(ex.Response.GetResponseStream(), Encoding.UTF8))
                    return await reader.ReadToEndAsync();
            }
        }

        // ---------------------------------------------------------------------------------------

        private void Report(string headline, double? fraction, string detail) => Stage?.Invoke(headline, fraction, detail);

        private void Say(string line)
        {
            lock (_log) _log.AppendLine(line);
            Line?.Invoke(line);
        }

        private static string Quote(string s) => "\"" + s.Replace("\"", "\\\"") + "\"";

        private static void KillTree(int pid)
        {
            // powershell -> python -> pip: stopping only the first leaves the download running.
            Process.Start(new ProcessStartInfo("taskkill", $"/PID {pid} /T /F")
            { CreateNoWindow = true, UseShellExecute = false })?.WaitForExit(5000);
        }
    }

    internal sealed class InstallException : Exception
    {
        public InstallException(string message) : base(message) { }
    }
}
