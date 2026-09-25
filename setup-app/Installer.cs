using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Reflection;
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
        /// <summary>Where the big things go -- Studio, the Qwen model, Breeze. Empty: their usual places.</summary>
        public string DataDir = "";
        public bool DesktopIcon = true;
        public bool ForClaudeCode;
        /// <summary>No window anywhere: the caller (a game) shows the progress from the status file.</summary>
        public bool Quiet;
        public string Source = Installer.DefaultSource;
    }

    /// <summary>
    /// Puts claude-voice on the machine: the code, then whatever the chosen engine needs, then the
    /// engine running.
    /// <para>
    /// It installs NOTHING itself beyond copying files. Every real step -- Python, Studio, the
    /// models, pocket-tts, Breeze's environment -- is setup.ps1's, run exactly as a person would run
    /// it, so this window and the command line can never disagree about what an install is. That
    /// script resumes where it stopped, which is what makes "Try again" an honest button.
    /// </para>
    /// <para>
    /// FOUR STEPS, whatever the engine, because that is what a person can hold in their head while
    /// something downloads: the app, the speech engine, the voice model, the first start. Each line
    /// the script prints is placed in one of them, and every download says how much, how fast and
    /// how long is left -- the same words in this window and, through <see cref="StatusFile"/>, in a
    /// game that started the install without a window.
    /// </para>
    /// </summary>
    internal sealed class Installer
    {
        /// <summary>The code of the release this setup came from; <c>main</c> if that tag is not there.</summary>
        public static string DefaultSource => "https://github.com/TraxData313/claude-voice/archive/refs/tags/v" + Version + ".zip";
        public const string MainSource = "https://github.com/TraxData313/claude-voice/archive/refs/heads/main.zip";
        public const int Port = 8765;

        public static string Version
        {
            get
            {
                var v = Assembly.GetExecutingAssembly().GetName().Version;
                return v == null ? "0.0.0" : $"{v.Major}.{v.Minor}.{v.Build}";
            }
        }

        /// <summary>A headline for a person, how far along overall (0..1, null = "working on it"), and a detail line.</summary>
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

        // ------------------------------------------------------------------ the four steps

        public static readonly string[] Phases = { "app", "engine", "model", "start" };

        private InstallPlan _plan;
        private string _phase = "app";
        private DateTime _phaseStarted = DateTime.UtcNow;
        private string _headline = "", _detail = "";
        private double? _phaseFraction;
        private readonly DateTime _started = DateTime.UtcNow;
        /// <summary>Everything is installed and it only waits for the engine to answer: a caller may treat
        /// the app answering now as the end. (Breeze's own first start is not this.)</summary>
        private bool _final;

        public async Task RunAsync(InstallPlan plan, CancellationToken outer)
        {
            _plan = plan;
            StatusFile.ClearCancel();
            StatusFile.AppendLog($"{Environment.NewLine}=== {DateTime.Now:yyyy-MM-dd HH:mm:ss} setup {Version}, engine {plan.Engine.Id}, into {plan.Folder}" +
                                 (plan.DataDir.Length > 0 ? $", voice files in {plan.DataDir}" : "") + Environment.NewLine);
            using (var cts = CancellationTokenSource.CreateLinkedTokenSource(outer))
            {
                var cancel = cts.Token;
                // A game asks to stop by leaving a file; this is the only ear it has.
                var watcher = Task.Run(async () =>
                {
                    while (!cancel.IsCancellationRequested)
                    {
                        if (StatusFile.CancelRequested()) { Say("Asked to stop."); cts.Cancel(); break; }
                        try { await Task.Delay(700, cancel); } catch { break; }
                    }
                });

                try
                {
                    await RunStepsAsync(plan, cancel);
                    Report("start", "The voices are ready.", 1, "");
                    Finish("done", "");
                }
                catch (OperationCanceledException)
                {
                    Finish("cancelled", "Stopped. Nothing is lost — installing again carries on from where it got to.");
                    throw;
                }
                catch (Exception ex)
                {
                    Finish("failed", ex is InstallException ? ex.Message : "Something unexpected went wrong: " + ex.Message);
                    throw;
                }
                finally
                {
                    cts.Cancel();
                    try { await watcher; } catch { }
                }
            }
        }

        private async Task RunStepsAsync(InstallPlan plan, CancellationToken cancel)
        {
            Say($"Installing claude-voice into {plan.Folder}, engine {plan.Engine.Id}.");
            Directory.CreateDirectory(plan.Folder);
            if (plan.DataDir.Length > 0) Directory.CreateDirectory(plan.DataDir);

            // --- the app ----------------------------------------------------------------------
            var gitCheckout = Directory.Exists(Path.Combine(plan.Folder, ".git"));
            if (gitCheckout)
            {
                // Somebody's working copy. Its code is theirs and is updated by git, never by a zip
                // laid over the top of their changes.
                Say("That folder is a git checkout, so its code is left as it is.");
            }
            else
            {
                Report("app", "Downloading the voice app", 0, "");
                await StopRunningEngineAsync();
                await FetchCodeAsync(plan, cancel);
            }

            // --- the engine and its model -------------------------------------------------------
            cancel.ThrowIfCancellationRequested();
            Report("engine", EngineHeadline(plan.Engine), null, "");
            await RunSetupAsync(plan, cancel);

            // --- the first start --------------------------------------------------------------
            _final = true;
            Report("start", "Starting the voices for the first time", null,
                   plan.Engine.Id == "pocket"
                       ? "Pocket fetches its small model now and wakes up — a minute or two."
                       : "Loading the model into your graphics card — up to a minute.");
            if (!await WaitForEngineAsync(plan.Engine.Id, TimeSpan.FromMinutes(plan.Engine.Id == "pocket" ? 8 : 5), cancel))
                throw new InstallException("Everything is installed, but the voices did not start. Try starting them again — " +
                                           "if it keeps happening, the details are in " + StatusFile.LogPath);
            if (!plan.Quiet) await SayHelloAsync();
        }

        private static string EngineHeadline(EngineInfo engine) =>
            engine.Id == "breeze" ? "Setting up Breeze" :
            engine.Id == "pocket" ? "Setting up Pocket" : "Setting up Qwen";

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
                    source = plan.Source;             // a local copy -- how this is tested
                    Say($"Copying from {source}.");
                }
                else
                {
                    var zip = Path.Combine(temp, "claude-voice.zip");
                    try
                    {
                        await DownloadAsync(plan.Source, zip, cancel);
                    }
                    catch (WebException ex) when (plan.Source != MainSource &&
                                                  (ex.Response as HttpWebResponse)?.StatusCode == HttpStatusCode.NotFound)
                    {
                        // A setup built outside a release has no tag of its own to fetch.
                        Say($"{plan.Source} is not there; taking the newest code instead.");
                        await DownloadAsync(MainSource, zip, cancel);
                    }
                    var unpacked = Path.Combine(temp, "unpacked");
                    ZipFile.ExtractToDirectory(zip, unpacked);
                    // GitHub wraps the tree in one folder named after the branch or tag.
                    var inner = Directory.GetDirectories(unpacked);
                    source = inner.Length == 1 && !File.Exists(Path.Combine(unpacked, "voice_lib.py")) ? inner[0] : unpacked;
                }

                if (!File.Exists(Path.Combine(source, "setup.ps1")))
                    throw new InstallException("The download did not hold claude-voice. Check your internet connection and try again.");

                try { CopyTree(source, plan.Folder); }
                catch (IOException ex)
                {
                    // A file the running app holds open cannot be replaced under it -- Pocket keeps a
                    // voice's weights mapped while it speaks. Only a file that changed is ever written
                    // (see CopyTree), so this is a real update: stop the app, and lay the code down again.
                    if (await RunningVersionAsync() == null) throw;
                    Say($"In use by the running voice app ({ex.Message}) -- stopping it to finish.");
                    await QuitRunningEngineAsync();
                    CopyTree(source, plan.Folder);
                }
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
            { ".git", "logs", "__pycache__", "config.json", "bin", "obj", "dist", "voices-local", "installed.json", ".pytest_cache" };

        private static void CopyTree(string from, string to)
        {
            Directory.CreateDirectory(to);
            foreach (var file in Directory.GetFiles(from))
            {
                if (NeverCopied.Contains(Path.GetFileName(file))) continue;
                var dest = Path.Combine(to, Path.GetFileName(file));
                // The same file is left where it is. Rewriting it changes nothing, and fails outright
                // when the running app has it open -- which, now that adding an engine leaves the app
                // running, is every Pocket voice it is speaking with (2026-09-25).
                if (SameFile(file, dest)) continue;
                File.Copy(file, dest, true);
            }
            foreach (var dir in Directory.GetDirectories(from))
            {
                if (NeverCopied.Contains(Path.GetFileName(dir))) continue;
                CopyTree(dir, Path.Combine(to, Path.GetFileName(dir)));
            }
        }

        /// <summary>Same length and same last-write time: a copy keeps the time, and so does a zip.</summary>
        private static bool SameFile(string a, string b)
        {
            try
            {
                var fa = new FileInfo(a);
                var fb = new FileInfo(b);
                return fb.Exists && fa.Length == fb.Length && fa.LastWriteTimeUtc == fb.LastWriteTimeUtc;
            }
            catch { return false; }
        }

        private async Task DownloadAsync(string url, string dest, CancellationToken cancel)
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
                        Report("app", "Downloading the voice app", total > 0 ? (double)done / total : (double?)null,
                               Amounts("claude-voice", done, total));
                    }
                }
            }
            Say("Fetched " + url);
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
            if (plan.DataDir.Length > 0) args.Append(" -DataDir ").Append(Quote(plan.DataDir));
            if (plan.Quiet) args.Append(" -Quiet");
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
            _setup.OutputDataReceived += (_, e) => { if (e.Data != null) Hear(e.Data, plan); };
            _setup.ErrorDataReceived += (_, e) => { if (e.Data != null) Say(e.Data); };
            _setup.Exited += (_, __) => exited.TrySetResult(_setup.ExitCode);
            _setup.Start();
            _setup.BeginOutputReadLine();
            _setup.BeginErrorReadLine();

            using (cancel.Register(() => { try { KillTree(_setup.Id); } catch { } }))
            {
                var code = await exited.Task;
                StopWatchingFolder();
                cancel.ThrowIfCancellationRequested();
                if (code != 0)
                    throw new InstallException(LastProblem() ?? $"The install stopped (code {code}).");
            }
        }

        private static readonly Regex Progress = new Regex(@"^@progress (.+)\|(\d+)\|(\d+)$");
        private static readonly Regex BreezeStep = new Regex(@"^\[(\d+)/(\d+)\]\s*(.+)$");

        /// <summary>Turns setup.ps1's own lines into something a person wants to read, in one of the four steps.</summary>
        private void Hear(string line, InstallPlan plan)
        {
            var text = line.TrimEnd();
            var p = Progress.Match(text);
            if (p.Success)
            {
                var label = p.Groups[1].Value.Trim();
                var done = long.Parse(p.Groups[2].Value, CultureInfo.InvariantCulture);
                var total = long.Parse(p.Groups[3].Value, CultureInfo.InvariantCulture);
                var (phase, headline, what) = Download(label);
                Report(phase, headline, total > 0 ? (double)done / total : (double?)null, Amounts(what, done, total));
                return;
            }

            Say(text);
            var b = BreezeStep.Match(text.Trim());
            if (b.Success)
            {
                var i = int.Parse(b.Groups[1].Value, CultureInfo.InvariantCulture);
                BreezeStepStarted(i, plan);
                return;
            }

            var lower = text.ToLowerInvariant();
            if (lower.StartsWith("python") && lower.Contains("installing"))
                Report("app", "Installing Python", null, "The language the voice app is written in — a copy just for it.");
            else if (lower.StartsWith("pocket-tts") && lower.Contains("installing"))
                Report("engine", "Installing Pocket", null, "About 1 GB. This part has no counter — a few minutes on a good connection.");
            else if (lower.StartsWith("studio") && (lower.Contains("unpack") || lower.Contains("moved")))
                Report("engine", "Unpacking the speech engine", null, "");
            else if (lower.StartsWith("studio") && (lower.Contains("already") || lower.Contains("found at")))
                Report("engine", "The speech engine is already here", 1, "Found on this computer — nothing to download.");
            else if (lower.StartsWith("model") && lower.Contains("have"))
                Report("model", "The voice model is already here", 1, "Found on this computer — nothing to download.");
        }

        private static (string phase, string headline, string what) Download(string label)
        {
            var l = label.ToLowerInvariant();
            if (l.Contains("talker")) return ("model", "Downloading the voice model", "Qwen voice model");
            if (l.Contains("tokenizer")) return ("model", "Downloading the sound decoder", "sound decoder");
            if (l.Contains("python")) return ("app", "Downloading Python", "Python");
            if (l.Contains("studio") || l.Contains(".msi") || l.Contains(".zip")) return ("engine", "Downloading the speech engine", "Qwen-TTS Studio");
            return ("engine", "Downloading " + label, label);
        }

        /// <summary>Breeze's own seven steps, placed among the four. Two of them are long downloads that print
        /// no numbers of their own; the model's is measured from its folder instead.</summary>
        private void BreezeStepStarted(int step, InstallPlan plan)
        {
            StopWatchingFolder();
            switch (step)
            {
                case 1: Report("engine", "Making Breeze a place to live", null, "Its own copy of Python, kept apart from everything else."); break;
                case 2:
                    Report("engine", "Downloading PyTorch, the engine Breeze runs on", null, "2.9 GB — usually 5 to 15 minutes.");
                    WatchPipProgress(plan, "engine", "Downloading PyTorch, the engine Breeze runs on", "PyTorch", bar: true);
                    break;
                case 3: Report("engine", "Checking PyTorch can see your graphics card", null, ""); break;
                case 4: Report("engine", "Downloading Breeze's code", null, ""); break;
                case 5:
                    Report("engine", "Installing what Breeze needs", null, "PyTorch is in. Now the smaller pieces around it — a few hundred MB.");
                    WatchPipProgress(plan, "engine", "Installing what Breeze needs", null, bar: false);
                    break;
                case 6:
                    Report("model", "Downloading the Breeze voice model", 0, "7.7 GB — the long part. It carries on if the connection drops.");
                    WatchFolderGrow(BreezeWeightsFolder(plan), 7_700_000_000L, "model", "Downloading the Breeze voice model", "Breeze model");
                    break;
                case 7: Report("start", "Starting Breeze for the first time", null,
                               "It tunes itself to your graphics card — a few minutes, once."); break;
            }
        }

        /// <summary>Where Breeze's weights land: the chosen folder, or wherever breeze_setup wrote down it went.</summary>
        private static string BreezeWeightsFolder(InstallPlan plan)
        {
            if (plan.DataDir.Length > 0) return Path.Combine(plan.DataDir, "breeze", "weights");
            try
            {
                var status = Path.Combine(plan.Folder, "logs", "breeze-install.json");
                var doc = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(status));
                if (doc != null && doc.TryGetValue("target", out var t) && t is string target) return Path.Combine(target, "weights");
            }
            catch { }
            return null;
        }

        private CancellationTokenSource _folderWatch;

        private void WatchFolderGrow(string folder, long expected, string phase, string headline, string what)
        {
            if (string.IsNullOrEmpty(folder)) return;
            var cts = new CancellationTokenSource();
            _folderWatch = cts;
            Task.Run(async () =>
            {
                while (!cts.IsCancellationRequested)
                {
                    try { await Task.Delay(2000, cts.Token); } catch { break; }
                    var have = FolderSize(folder);
                    if (have <= 0) continue;
                    Report(phase, headline, Math.Min(0.99, (double)have / expected), Amounts(what, have, expected));
                }
            });
        }

        /// <summary>pip's own count, from the "Progress n of m" lines breeze_setup has it write into
        /// Breeze's install log. PyTorch's 2.9 GB used to show no movement at all, for long enough to
        /// look stuck (2026-09-25). Without <paramref name="bar"/> only the words move: after PyTorch
        /// come many small packages, and a bar starting over for each one reads as going backwards.
        /// <paramref name="what"/> names the download; null names each package as pip reaches it.</summary>
        private void WatchPipProgress(InstallPlan plan, string phase, string headline, string what, bool bar)
        {
            var log = Path.Combine(plan.Folder, "logs", "breeze-install.log");
            var from = FileLength(log);      // only what this step writes
            var cts = new CancellationTokenSource();
            _folderWatch = cts;
            Task.Run(async () =>
            {
                while (!cts.IsCancellationRequested)
                {
                    try { await Task.Delay(1000, cts.Token); } catch { break; }
                    var (name, done, total) = LastPipProgress(log, from);
                    if (total <= 0 || cts.IsCancellationRequested) continue;
                    var label = what ?? name;
                    var amounts = Amounts(label, done, total);
                    Report(phase, headline, bar ? Math.Min(0.99, (double)done / total) : (double?)null,
                           what == null && name.Length > 0 ? name + ": " + amounts : amounts);
                }
            });
        }

        private static long FileLength(string path)
        {
            try { return File.Exists(path) ? new FileInfo(path).Length : 0; } catch { return 0; }
        }

        private static readonly Regex PipProgress = new Regex(@"^Progress (\d+) of (\d+)", RegexOptions.Multiline);
        private static readonly Regex PipDownloading = new Regex(@"^\s*Downloading (\S+?)-\d", RegexOptions.Multiline);

        /// <summary>The package pip is fetching and how far it has got, read from the end of the log
        /// (shared with the writer, which still has it open).</summary>
        private static (string name, long done, long total) LastPipProgress(string log, long from)
        {
            try
            {
                using (var fs = new FileStream(log, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    var start = Math.Max(from, fs.Length - 65536);
                    if (start >= fs.Length) return ("", 0, 0);
                    fs.Seek(start, SeekOrigin.Begin);
                    string tail;
                    using (var reader = new StreamReader(fs, Encoding.UTF8)) tail = reader.ReadToEnd();
                    var progress = PipProgress.Matches(tail);
                    if (progress.Count == 0) return ("", 0, 0);
                    var last = progress[progress.Count - 1];
                    var names = PipDownloading.Matches(tail.Substring(0, last.Index));
                    var name = names.Count > 0 ? Uri.UnescapeDataString(names[names.Count - 1].Groups[1].Value) : "";
                    name = name.Substring(name.LastIndexOf('/') + 1);   // an index other than PyPI prints the whole URL
                    return (name,
                            long.Parse(last.Groups[1].Value, CultureInfo.InvariantCulture),
                            long.Parse(last.Groups[2].Value, CultureInfo.InvariantCulture));
                }
            }
            catch { return ("", 0, 0); }
        }

        private void StopWatchingFolder()
        {
            try { _folderWatch?.Cancel(); } catch { }
            _folderWatch = null;
        }

        private static long FolderSize(string folder)
        {
            try
            {
                if (!Directory.Exists(folder)) return 0;
                long sum = 0;
                foreach (var f in Directory.EnumerateFiles(folder, "*", SearchOption.AllDirectories))
                    try { sum += new FileInfo(f).Length; } catch { }
                return sum;
            }
            catch { return 0; }
        }

        // ------------------------------------------------------------------ amounts, speed, time left

        private string _rateWhat = "";
        private long _rateBytes;
        private DateTime _rateAt;
        private double _rate;           // bytes a second, smoothed

        /// <summary>"1.2 of 2.2 GB · 14 MB/s · about 2 min left" -- measured, not promised.</summary>
        private string Amounts(string what, long done, long total)
        {
            var now = DateTime.UtcNow;
            if (what != _rateWhat || done < _rateBytes)
            {
                _rateWhat = what; _rateBytes = done; _rateAt = now; _rate = 0;
            }
            else
            {
                var secs = (now - _rateAt).TotalSeconds;
                if (secs >= 1)
                {
                    var instant = (done - _rateBytes) / secs;
                    _rate = _rate <= 0 ? instant : _rate * 0.7 + instant * 0.3;
                    _rateBytes = done; _rateAt = now;
                }
            }

            var parts = new List<string> { total > 0 ? $"{Size(done)} of {Size(total)}" : Size(done) };
            if (_rate > 50_000) parts.Add(string.Format(CultureInfo.InvariantCulture, "{0:0.#} MB/s", _rate / 1_000_000));
            if (_rate > 50_000 && total > done) parts.Add(TimeLeft((total - done) / _rate));
            return string.Join(" · ", parts);
        }

        public static string Size(long bytes) =>
            bytes >= 1_000_000_000
                ? string.Format(CultureInfo.InvariantCulture, "{0:0.0} GB", bytes / 1_000_000_000.0)
                : string.Format(CultureInfo.InvariantCulture, "{0:0} MB", bytes / 1_000_000.0);

        private static string TimeLeft(double seconds)
        {
            if (seconds < 50) return "less than a minute left";
            var minutes = (int)Math.Round(seconds / 60);
            if (minutes < 60) return $"about {minutes} min left";
            return string.Format(CultureInfo.InvariantCulture, "about {0:0.#} hours left", minutes / 60.0);
        }

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
            // know the question. The same code is no reason to go quiet, though: adding an engine
            // from a game (Breeze, twenty minutes of downloading) used to silence the voices it
            // already had for all of it, and stopping that install then left nothing running at
            // all (2026-09-25). setup.ps1 moves the app onto the new engine once it is there.
            var running = await RunningVersionAsync();
            if (running == null || running == Version) return;
            await QuitRunningEngineAsync();
        }

        /// <summary>Asks the app to go, and waits until it has -- a file it held is only free once
        /// its process has ended, not when it has said yes.</summary>
        private static async Task QuitRunningEngineAsync()
        {
            try { await PostAsync("/quit", "{}", TimeSpan.FromSeconds(3)); } catch { }
            for (var i = 0; i < 20 && await RunningVersionAsync() != null; i++)
                await Task.Delay(500);
            await Task.Delay(1000);
        }

        /// <summary>The version the app answering on the port gives for itself: null when nothing
        /// answers, empty when it is too old to say.</summary>
        private static async Task<string> RunningVersionAsync()
        {
            try
            {
                var json = await PostAsync("/health", "{}", TimeSpan.FromSeconds(2));
                if (json == null) return null;
                var doc = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
                return doc != null && doc.TryGetValue("version", out var v) && v is string s ? s : "";
            }
            catch { return null; }
        }

        private static async Task<bool> WaitForEngineAsync(string engine, TimeSpan patience, CancellationToken cancel)
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
                        // Ready with the engine just installed -- not the one that went on talking
                        // through the install, which would answer "ready" before this one had loaded.
                        if (doc != null && doc.TryGetValue("ready", out var ready) && ready is bool b && b &&
                            (!doc.TryGetValue("engineLoaded", out var loaded) || !(loaded is string l) || l.Length == 0 || l == engine))
                            return true;
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

        private void Report(string phase, string headline, double? phaseFraction, string detail)
        {
            if (phase != _phase) { _phase = phase; _phaseStarted = DateTime.UtcNow; }
            _headline = headline;
            _phaseFraction = phaseFraction;
            _detail = detail ?? "";
            Stage?.Invoke(headline, Overall(phase, phaseFraction), _detail);
            WriteStatus("running", "", force: false);
        }

        /// <summary>The whole install as one bar: how much of the waiting each step usually is, per engine.</summary>
        private double? Overall(string phase, double? within)
        {
            var id = _plan?.Engine?.Id ?? "qwen";
            // start of each phase, in the order app, engine, model, start, end
            double[] edges =
                id == "breeze" ? new[] { 0, 0.03, 0.35, 0.93, 1.0 } :
                id == "pocket" ? new[] { 0, 0.10, 0.60, 0.60, 1.0 } :
                                 new[] { 0, 0.06, 0.30, 0.92, 1.0 };
            var i = Array.IndexOf(Phases, phase);
            if (i < 0) return null;
            var from = edges[i];
            var to = edges[i + 1];
            return from + (to - from) * (within ?? 0);
        }

        private void Finish(string state, string error)
        {
            if (state == "done") { _phase = "start"; _phaseFraction = 1; }
            WriteStatus(state, error, force: true);
            StatusFile.AppendLog(Log + $"--- {state}{(error.Length > 0 ? ": " + error : "")}{Environment.NewLine}");
        }

        private void WriteStatus(string state, string error, bool force)
        {
            var plan = _plan;
            StatusFile.Write(new Dictionary<string, object>
            {
                { "state", state },
                { "pid", Process.GetCurrentProcess().Id },
                { "version", Version },
                { "engine", plan?.Engine?.Id ?? "" },
                { "folder", plan?.Folder ?? "" },
                { "data", plan?.DataDir ?? "" },
                { "quiet", plan?.Quiet ?? false },
                { "phase", _phase },
                { "final", _final },
                { "phaseStarted", _phaseStarted.ToString("o", CultureInfo.InvariantCulture) },
                { "started", _started.ToString("o", CultureInfo.InvariantCulture) },
                { "headline", _headline },
                { "detail", _detail },
                { "phaseFraction", _phaseFraction },
                { "fraction", Overall(_phase, _phaseFraction) },
                { "error", error },
                { "log", StatusFile.LogPath },
                // Two things an install started by a game has failed to leave behind while every run
                // from a shell left both (2026-09-25): its lines in setup.log, and its entry in
                // Settings > Apps. The log cannot say why it could not be written, so this file does.
                { "logError", StatusFile.LogError },
                { "logTail", StatusFile.LogError.Length > 0 ? LastLines(Log, 40) : "" },
                { "appsEntry", ListedInApps() },
            }, force);
        }

        private static string LastLines(string text, int count)
        {
            var lines = text.Split('\n');
            return string.Join("\n", lines, Math.Max(0, lines.Length - count), Math.Min(count, lines.Length));
        }

        /// <summary>Whether Settings > Apps lists claude-voice, asked from this process's own view of
        /// the registry -- the view a game's child has, which is the one in question.</summary>
        private static bool ListedInApps()
        {
            try
            {
                using (var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                    @"Software\Microsoft\Windows\CurrentVersion\Uninstall\claude-voice"))
                    return key != null;
            }
            catch { return false; }
        }

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
