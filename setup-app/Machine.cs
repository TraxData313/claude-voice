using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text.RegularExpressions;

namespace ClaudeVoiceSetup
{
    /// <summary>The graphics card, as nvidia-smi describes it. Null means there is none it can see.</summary>
    internal sealed class Gpu
    {
        public string Name = "";
        public int VramMiB;
        public double Compute;      // 8.6 for an RTX 30-series, 0 when the driver is too old to say
        public double Cuda;         // the newest CUDA the driver can run, from nvidia-smi's own banner

        public string Describe()
        {
            var gb = VramMiB > 0 ? $" ({Math.Round(VramMiB / 1024.0)} GB)" : "";
            return Name + gb;
        }
    }

    internal enum Fit { Great, Slow, No }

    /// <summary>What one engine asks of a machine, and what it gives back. The numbers are the
    /// repo's own, measured on real cards -- see docs/engines.md -- not the vendors'.</summary>
    internal sealed class EngineInfo
    {
        public string Id = "";
        public string Title = "";
        public string Tagline = "";
        public string Needs = "";
        public string Languages = "";
        public double DownloadGb;
        public double DiskGb;
        public bool EnglishOnly;
        public bool ReadsEveryAlphabet;

        public static readonly EngineInfo Breeze = new EngineInfo
        {
            Id = "breeze",
            Title = "Breeze — the actor",
            Tagline = "Laughs, sighs and whispers where the words call for it. The most alive of the three.",
            Needs = "NVIDIA card with 16 GB (RTX 30-series or newer)",
            Languages = "English",
            DownloadGb = 11, DiskGb = 20, EnglishOnly = true,
        };

        public static readonly EngineInfo Qwen = new EngineInfo
        {
            Id = "qwen",
            Title = "Qwen — the storyteller",
            Tagline = "Warm, natural voices that read any language — Bulgarian and Russian included.",
            Needs = "NVIDIA card with 4 GB or more",
            Languages = "Any language",
            DownloadGb = 3, DiskGb = 3.5, ReadsEveryAlphabet = true,
        };

        public static readonly EngineInfo Pocket = new EngineInfo
        {
            Id = "pocket",
            Title = "Pocket — the light one",
            Tagline = "Runs on any computer, no graphics card at all. Quick to answer, a little plainer.",
            Needs = "Any Windows PC",
            Languages = "English + 5 European languages",
            DownloadGb = 1, DiskGb = 1.5,
        };

        public static readonly EngineInfo[] All = { Breeze, Qwen, Pocket };

        public static EngineInfo ById(string id)
        {
            foreach (var e in All)
                if (string.Equals(e.Id, id, StringComparison.OrdinalIgnoreCase)) return e;
            return null;
        }
    }

    internal sealed class Verdict
    {
        public Fit Fit;
        public string Why = "";
    }

    internal static class Machine
    {
        /// <summary>breeze_setup.py's own lines, so the two can never disagree about a card.</summary>
        private const int BreezeNeedMiB = 11_500;
        private const int BreezeFullSpeedMiB = 15_500;
        private const double BreezeNeedCompute = 8.0;
        private const double BreezeNeedCuda = 12.8;

        /// <summary>An estimate, and docs/install.md says so: the Qwen weights are 2.2 GB and want room to work.</summary>
        private const int QwenNeedMiB = 3_500;

        public static Gpu DetectNvidia()
        {
            var smi = FindSmi();
            if (smi == null) return null;
            try
            {
                var rows = Run(smi, "--query-gpu=name,memory.total,compute_cap --format=csv,noheader,nounits");
                Gpu best = null;
                foreach (var line in rows.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries))
                {
                    var parts = line.Split(',');
                    if (parts.Length < 2) continue;
                    var gpu = new Gpu { Name = parts[0].Trim() };
                    int.TryParse(parts[1].Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out gpu.VramMiB);
                    if (parts.Length > 2)
                        double.TryParse(parts[2].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out gpu.Compute);
                    if (best == null || gpu.VramMiB > best.VramMiB) best = gpu;
                }
                if (best == null) return null;

                // The banner says which CUDA the driver can run; the query flags do not.
                var banner = Run(smi, "");
                var m = Regex.Match(banner, @"CUDA Version:\s*([0-9]+\.[0-9]+)");
                if (m.Success)
                    double.TryParse(m.Groups[1].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out best.Cuda);
                return best;
            }
            catch
            {
                return null;
            }
        }

        /// <summary>A driver with no nvidia-smi beside it still means an NVIDIA card is there.</summary>
        public static bool HasNvidiaDriver =>
            File.Exists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "nvcuda.dll"));

        public static Verdict Judge(EngineInfo engine, Gpu gpu, double freeGb)
        {
            var nvidia = gpu != null || HasNvidiaDriver;
            if (freeGb > 0 && freeGb < engine.DiskGb + 1)
                return new Verdict { Fit = Fit.No, Why = $"Needs about {engine.DiskGb:0.#} GB free — that drive has {freeGb:0.#} GB." };

            if (engine == EngineInfo.Pocket) return new Verdict { Fit = Fit.Great };

            if (!nvidia)
                return new Verdict { Fit = Fit.No, Why = "Needs an NVIDIA graphics card, and none was found." };

            if (engine == EngineInfo.Qwen)
            {
                if (gpu != null && gpu.VramMiB > 0 && gpu.VramMiB < QwenNeedMiB)
                    return new Verdict { Fit = Fit.Slow, Why = "Your card is small for it — it may not fit. Pocket is the safe choice." };
                return new Verdict { Fit = Fit.Great };
            }

            // Breeze
            if (gpu == null)
                return new Verdict { Fit = Fit.No, Why = "Could not read your card's memory — Breeze needs 12 GB or more." };
            if (gpu.Compute > 0 && gpu.Compute < BreezeNeedCompute)
                return new Verdict { Fit = Fit.No, Why = "Needs an RTX 30-series card or newer." };
            if (gpu.VramMiB < BreezeNeedMiB)
                return new Verdict { Fit = Fit.No, Why = $"Needs 12 GB of video memory — yours has {Math.Round(gpu.VramMiB / 1024.0)} GB." };
            if (gpu.Cuda > 0 && gpu.Cuda < BreezeNeedCuda)
                return new Verdict { Fit = Fit.No, Why = "Your graphics driver is too old for it — update it from nvidia.com, then run this again." };
            if (gpu.VramMiB < BreezeFullSpeedMiB)
                return new Verdict { Fit = Fit.Slow, Why = "Fits, but on a card under 16 GB it speaks a little slower than you read." };
            return new Verdict { Fit = Fit.Great };
        }

        /// <summary>The best engine this machine runs well, for the language the voices will speak.</summary>
        public static EngineInfo Recommend(Gpu gpu, double freeGb, bool english)
        {
            if (english && Judge(EngineInfo.Breeze, gpu, freeGb).Fit == Fit.Great) return EngineInfo.Breeze;
            if (Judge(EngineInfo.Qwen, gpu, freeGb).Fit == Fit.Great) return EngineInfo.Qwen;
            return EngineInfo.Pocket;
        }

        public static double FreeGb(string folder)
        {
            try
            {
                var root = Path.GetPathRoot(Path.GetFullPath(folder));
                return new DriveInfo(root).AvailableFreeSpace / (1024.0 * 1024 * 1024);
            }
            catch { return 0; }
        }

        public static bool HasClaudeCode =>
            Directory.Exists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".claude"));

        private static string FindSmi()
        {
            var candidates = new[]
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "nvidia-smi.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                             "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe"),
            };
            foreach (var c in candidates) if (File.Exists(c)) return c;
            return null;
        }

        private static string Run(string exe, string args)
        {
            using (var p = Process.Start(new ProcessStartInfo(exe, args)
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
            }))
            {
                var text = p.StandardOutput.ReadToEnd();
                p.WaitForExit(10_000);
                return text;
            }
        }
    }
}
