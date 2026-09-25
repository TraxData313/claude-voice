using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Web.Script.Serialization;

namespace ClaudeVoiceSetup
{
    /// <summary>
    /// What the install is doing, written where anything else on this machine can read it:
    /// <c>%LOCALAPPDATA%\claude-voice\setup-status.json</c>.
    /// <para>
    /// The Immersive AI mod runs this program with no window of its own (<c>--quiet</c>) and draws the
    /// progress inside the game from this file: which of four steps it is on, what it is fetching,
    /// how much of it, how fast, and how long is left. The window writes it too, so a game opened
    /// while somebody installs by double-click still knows what is going on.
    /// </para>
    /// <para>
    /// A game can ask the install to stop by leaving a file named <c>setup-cancel</c> beside it.
    /// Stopping loses nothing: the next run carries on where this one got to.
    /// </para>
    /// </summary>
    internal static class StatusFile
    {
        public static string Folder =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "claude-voice");

        public static string PathOf => System.IO.Path.Combine(Folder, "setup-status.json");
        public static string CancelPath => System.IO.Path.Combine(Folder, "setup-cancel");
        public static string LogPath => System.IO.Path.Combine(Folder, "setup.log");

        private static readonly object Gate = new object();
        private static DateTime _lastWrite = DateTime.MinValue;

        /// <summary>Writes the whole state. Throttled to a few times a second unless <paramref name="force"/>.</summary>
        public static void Write(Dictionary<string, object> fields, bool force = false)
        {
            lock (Gate)
            {
                if (!force && (DateTime.UtcNow - _lastWrite).TotalMilliseconds < 400) return;
                _lastWrite = DateTime.UtcNow;
                try
                {
                    Directory.CreateDirectory(Folder);
                    fields["updated"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
                    var json = new JavaScriptSerializer().Serialize(fields);
                    var tmp = PathOf + ".tmp";
                    File.WriteAllText(tmp, json);
                    if (File.Exists(PathOf)) File.Replace(tmp, PathOf, null);
                    else File.Move(tmp, PathOf);
                }
                catch { /* a status nobody can read is not a failed install */ }
            }
        }

        /// <summary>Whether a caller asked the install to stop. Consumes the request.</summary>
        public static bool CancelRequested()
        {
            try
            {
                if (!File.Exists(CancelPath)) return false;
                File.Delete(CancelPath);
                return true;
            }
            catch { return false; }
        }

        public static void ClearCancel()
        {
            try { if (File.Exists(CancelPath)) File.Delete(CancelPath); } catch { }
        }

        /// <summary>Another install already running, by the pid in the file. Null when none.</summary>
        public static int? RunningPid()
        {
            try
            {
                if (!File.Exists(PathOf)) return null;
                var doc = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(PathOf));
                if (doc == null || !(doc.TryGetValue("state", out var s) && (s as string) == "running")) return null;
                if (!doc.TryGetValue("pid", out var p)) return null;
                var pid = Convert.ToInt32(p, CultureInfo.InvariantCulture);
                if (pid == System.Diagnostics.Process.GetCurrentProcess().Id) return null;
                try { return System.Diagnostics.Process.GetProcessById(pid).HasExited ? (int?)null : pid; }
                catch { return null; }
            }
            catch { return null; }
        }

        /// <summary>Why the last append failed, empty when it did not. The status file carries it: a log
        /// that cannot be written is no place to say so.</summary>
        public static string LogError { get; private set; } = "";

        public static void AppendLog(string text)
        {
            try
            {
                Directory.CreateDirectory(Folder);
                File.AppendAllText(LogPath, text);
                LogError = "";
            }
            catch (Exception ex) { LogError = ex.GetType().Name + ": " + ex.Message; }
        }
    }
}
