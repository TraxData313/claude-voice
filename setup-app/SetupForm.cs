using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace ClaudeVoiceSetup
{
    /// <summary>
    /// Four pages and no jargon. The reader may never have heard of text-to-speech, a GPU or Python,
    /// and should not need to: they pick the card that says "recommended", press Install, and hear
    /// Abby say hello. Everything technical is one "Show details" away and no nearer.
    /// </summary>
    internal sealed class SetupForm : Form
    {
        private readonly Options _options;
        private readonly Image _art;
        private readonly Panel _content = new Panel();
        private Panel _page;

        private Gpu _gpu;
        private string _folder;
        private bool _english = true;
        private EngineCard[] _cards = new EngineCard[0];
        private EngineCard _chosen;
        private bool _userPicked;
        private CheckBox _desktop, _claude;
        private Label _folderLabel, _machineLabel;

        private Installer _installer;
        private CancellationTokenSource _cancel;
        private bool _installing;

        public SetupForm(Options options)
        {
            _options = options;
            using (var g = CreateGraphics()) Dpi.Scale = g.DpiX / 96f;

            Text = "claude-voice setup";
            Icon = LoadIcon();
            _art = LoadImage("abby.jpg");
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Palette.Paper;
            ForeColor = Palette.Ink;
            Font = new Font("Segoe UI", 10f);
            AutoScaleMode = AutoScaleMode.None;
            ClientSize = new Size(Dpi.Px(880), Dpi.Px(600));
            DoubleBuffered = true;

            _content.SetBounds(Dpi.Px(300), 0, ClientSize.Width - Dpi.Px(300), ClientSize.Height);
            _content.BackColor = Palette.Paper;
            Controls.Add(_content);

            _folder = options.Folder ?? Installer.ExistingInstall() ?? Installer.DefaultFolder;
            ShowWelcome();
            if (options.Show != null) ShowForScreenshot(options.Show);
            if (options.Auto)
                Shown += (_, __) =>
                {
                    var engine = EngineInfo.ById(options.Engine ?? "") ?? Machine.Recommend(Machine.DetectNvidia(), Machine.FreeGb(_folder), true);
                    ShowProgress(new InstallPlan
                    {
                        Engine = engine, Folder = _folder, DesktopIcon = false,
                        Source = options.Source ?? Installer.DefaultSource,
                    });
                };
            if (options.Shot != null)
                Shown += async (_, __) =>
                {
                    await Task.Delay(1500);
                    using (var bmp = new Bitmap(ClientSize.Width, ClientSize.Height))
                    {
                        DrawToBitmap(bmp, new Rectangle(Point.Empty, ClientSize));
                        bmp.Save(options.Shot);
                    }
                    Close();
                };

            // The card is asked about off the window's thread: nvidia-smi takes half a second, and a
            // window that appears half a second late looks like it did not open at all.
            Task.Run(() => Machine.DetectNvidia()).ContinueWith(t =>
            {
                _gpu = t.Status == TaskStatus.RanToCompletion ? t.Result : null;
                if (IsHandleCreated) BeginInvoke((Action)(() => { if (_cards.Length > 0) Rejudge(); }));
            });
        }

        // ------------------------------------------------------------------ the painting

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            var g = e.Graphics;
            var pane = new Rectangle(0, 0, Dpi.Px(300), ClientSize.Height);
            if (_art != null)
            {
                // Cover the pane, cropped around her face (a little left of centre in the painting).
                g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                var scale = (float)pane.Height / _art.Height;
                var srcW = pane.Width / scale;
                var faceX = _art.Width * 0.5f;
                var srcX = Math.Max(0, Math.Min(_art.Width - srcW, faceX - srcW / 2));
                g.DrawImage(_art, pane, new RectangleF(srcX, 0, srcW, _art.Height), GraphicsUnit.Pixel);
            }

            // A soft fade at the foot so the name sits on the picture rather than over it.
            var fade = new Rectangle(0, pane.Height - Dpi.Px(150), pane.Width, Dpi.Px(150));
            using (var b = new LinearGradientBrush(fade, Color.FromArgb(0, 30, 24, 20), Color.FromArgb(190, 30, 24, 20), 90f))
                g.FillRectangle(b, fade);
            using (var big = new Font("Segoe UI Semibold", 18f))
            using (var small = new Font("Segoe UI", 9.5f))
            {
                TextRenderer.DrawText(g, "claude-voice", big, new Point(Dpi.Px(22), pane.Height - Dpi.Px(82)), Color.White);
                TextRenderer.DrawText(g, "Natural voices, made on your own computer.", small,
                    new Point(Dpi.Px(24), pane.Height - Dpi.Px(44)), Color.FromArgb(235, 228, 216));
            }
        }

        // ------------------------------------------------------------------ pages

        private Panel NewPage(string title)
        {
            if (_page != null) { _content.Controls.Remove(_page); _page.Dispose(); }
            _page = new Panel { Dock = DockStyle.Fill, BackColor = Palette.Paper };
            _page.Controls.Add(new Label
            {
                Text = title,
                Font = new Font("Segoe UI Semibold", 20f),
                ForeColor = Palette.Ink,
                AutoSize = true,
                Location = new Point(Dpi.Px(40), Dpi.Px(34)),
            });
            _content.Controls.Add(_page);
            return _page;
        }

        private Label Text_(Panel page, string text, int top, float size = 10.5f, Color? color = null, bool bold = false, int height = 0)
        {
            var width = _content.Width - Dpi.Px(80);
            var label = new Label
            {
                Text = text,
                Font = new Font(bold ? "Segoe UI Semibold" : "Segoe UI", size),
                ForeColor = color ?? Palette.Ink,
                Location = new Point(Dpi.Px(40), top),
                MaximumSize = new Size(width, 0),
                AutoSize = height == 0,
            };
            if (height > 0) label.Size = new Size(width, height);
            page.Controls.Add(label);
            return label;
        }

        private NiceButton Button_(Panel page, string text, bool primary, int right, Action click, int width = 150)
        {
            var b = new NiceButton(text, primary) { Size = new Size(Dpi.Px(width), Dpi.Px(42)) };
            b.Location = new Point(_content.Width - Dpi.Px(40) - right - b.Width, _content.Height - Dpi.Px(40) - b.Height);
            b.Click += (_, __) => click();
            page.Controls.Add(b);
            return b;
        }

        private string ForWhom => string.IsNullOrWhiteSpace(_options.For) ? null : _options.For.Trim();

        private void ShowWelcome()
        {
            var page = NewPage("Hi! I'm Abby.");
            var y = Dpi.Px(92);
            var lead = ForWhom != null
                ? $"Let's give the characters in {ForWhom} a real voice."
                : "Let's give your computer a voice.";
            y = Text_(page, lead, y, 13f, Palette.Accent, bold: true).Bottom + Dpi.Px(14);
            y = Text_(page, "This sets up claude-voice — a small, free program that reads text aloud in natural " +
                            "voices, right here on your own computer.", y).Bottom + Dpi.Px(18);

            foreach (var line in new[]
            {
                "Free — no account and no subscription",
                "Private — nothing you hear ever leaves your computer",
                "No administrator rights, nothing to configure",
                "About 10 to 30 minutes, almost all of it downloading",
            })
            {
                page.Controls.Add(new Label
                {
                    Text = "✓", Font = new Font("Segoe UI Semibold", 11f), ForeColor = Palette.Good,
                    AutoSize = true, Location = new Point(Dpi.Px(42), y - Dpi.Px(1)),
                });
                y = Text_(page, line, y).Bottom + Dpi.Px(8);
                page.Controls[page.Controls.Count - 1].Left = Dpi.Px(66);
            }

            var existing = Installer.ExistingInstall();
            if (existing != null)
                Text_(page, "claude-voice is already on this computer. Carrying on will update it and add the voice you pick — " +
                            "your settings stay as they are.", y + Dpi.Px(14), 9.5f, Palette.Muted);

            Button_(page, "Let's start", true, 0, ShowChoose);
        }

        private void ShowChoose()
        {
            var page = NewPage("Pick a voice");
            _machineLabel = Text_(page, "", Dpi.Px(88), 9.5f, Palette.Muted);

            // Which language the voices will speak. It decides more than the card does: the engine
            // that acts best reads English only, and a player in Sofia deserves to hear that first.
            var row = new FlowLayoutPanel
            {
                Location = new Point(Dpi.Px(38), Dpi.Px(114)),
                Size = new Size(_content.Width - Dpi.Px(80), Dpi.Px(30)),
                BackColor = Palette.Paper,
                WrapContents = false,
            };
            row.Controls.Add(new Label { Text = "The voices will speak:", AutoSize = true, Margin = new Padding(0, Dpi.Px(5), Dpi.Px(8), 0), ForeColor = Palette.Ink });
            var en = new RadioButton { Text = "English", AutoSize = true, Checked = _english, ForeColor = Palette.Ink };
            var other = new RadioButton { Text = "Another language (Bulgarian, Russian, …)", AutoSize = true, Checked = !_english, ForeColor = Palette.Ink };
            en.CheckedChanged += (_, __) => { if (en.Checked) { _english = true; Rejudge(); } };
            other.CheckedChanged += (_, __) => { if (other.Checked) { _english = false; Rejudge(); } };
            row.Controls.Add(en);
            row.Controls.Add(other);
            page.Controls.Add(row);

            var y = Dpi.Px(152);
            var cards = new List<EngineCard>();
            foreach (var engine in EngineInfo.All)
            {
                var card = new EngineCard(engine)
                {
                    Location = new Point(Dpi.Px(40), y),
                    Width = _content.Width - Dpi.Px(80),
                };
                card.Picked += c => { _userPicked = true; Pick(c); };
                page.Controls.Add(card);
                cards.Add(card);
                y += card.Height + Dpi.Px(8);
            }
            _cards = cards.ToArray();

            _folderLabel = Text_(page, "", y + Dpi.Px(4), 9f, Palette.Muted);
            var change = new LinkLabel
            {
                Text = "Change…", AutoSize = true, LinkColor = Palette.Accent, ActiveLinkColor = Palette.AccentDark,
                Font = new Font("Segoe UI", 9f),
            };
            change.LinkClicked += (_, __) => ChangeFolder();
            page.Controls.Add(change);

            _desktop = new CheckBox
            {
                Text = "Put an Abby icon on my desktop, to open the voice's panel",
                Checked = true, AutoSize = true, ForeColor = Palette.Ink, Font = new Font("Segoe UI", 9.5f),
                Location = new Point(Dpi.Px(40), y + Dpi.Px(26)),
            };
            page.Controls.Add(_desktop);

            // Only offered to somebody who has Claude Code, and never ticked for somebody who came
            // here from a game: reading their coding sessions aloud is not what they asked for.
            if (Machine.HasClaudeCode)
            {
                _claude = new CheckBox
                {
                    Text = "Also read Claude Code's answers aloud",
                    Checked = ForWhom == null, AutoSize = true, ForeColor = Palette.Ink, Font = new Font("Segoe UI", 9.5f),
                    Location = new Point(Dpi.Px(40), y + Dpi.Px(48)),
                };
                page.Controls.Add(_claude);
            }

            Button_(page, "Install", true, 0, StartInstall);
            Button_(page, "Back", false, Dpi.Px(160), ShowWelcome, 110);
            Rejudge(pickRecommended: true);
            PlaceFolderLine(change);
        }

        private void PlaceFolderLine(LinkLabel change)
        {
            _folderLabel.Text = "Installs into  " + _folder;
            change.Location = new Point(_folderLabel.Right + Dpi.Px(6), _folderLabel.Top);
            if (change.Right > _content.Width - Dpi.Px(20))
            {
                _folderLabel.Text = "Installs into  …" + _folder.Substring(Math.Max(0, _folder.Length - 40));
                change.Location = new Point(_folderLabel.Right + Dpi.Px(6), _folderLabel.Top);
            }
        }

        private void Rejudge(bool pickRecommended = false)
        {
            if (_cards.Length == 0 || _machineLabel == null || _machineLabel.IsDisposed) return;
            var free = Machine.FreeGb(_folder);
            _machineLabel.Text = _gpu != null
                ? "Your graphics card:  " + _gpu.Describe()
                : Machine.HasNvidiaDriver ? "An NVIDIA graphics card is here, but its details could not be read."
                                          : "No NVIDIA graphics card found — that's fine, Pocket runs on any computer.";

            var best = Machine.Recommend(_gpu, free, _english);
            foreach (var card in _cards)
            {
                card.Verdict = Machine.Judge(card.Engine, _gpu, free);
                card.Recommended = card.Engine == best;
                card.LanguageMismatch = !_english && card.Engine.EnglishOnly && card.Runnable;
                card.Invalidate();
            }

            var wanted = EngineInfo.ById(_options.Engine ?? "");
            // Until somebody clicks a card, the choice follows the recommendation -- which moves once
            // the graphics card has been read, a moment after the page opens.
            var keep = _userPicked && _chosen != null && _chosen.Runnable && !pickRecommended && !_chosen.LanguageMismatch;
            if (!keep)
            {
                EngineCard pick = null;
                if (wanted != null) pick = Array.Find(_cards, c => c.Engine == wanted && c.Runnable);
                pick = pick ?? Array.Find(_cards, c => c.Recommended);
                Pick(pick);
            }
        }

        private void Pick(EngineCard card)
        {
            _chosen = card;
            foreach (var c in _cards) c.Selected = c == card;
        }

        private void ChangeFolder()
        {
            using (var dialog = new FolderBrowserDialog
            {
                Description = "Where should claude-voice live? A folder named claude-voice is made inside the one you pick.",
                SelectedPath = Directory.Exists(_folder) ? _folder : Path.GetDirectoryName(_folder),
            })
            {
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
                var picked = dialog.SelectedPath;
                _folder = string.Equals(Path.GetFileName(picked), "claude-voice", StringComparison.OrdinalIgnoreCase)
                          || File.Exists(Path.Combine(picked, "voice_lib.py"))
                    ? picked
                    : Path.Combine(picked, "claude-voice");
            }
            foreach (Control c in _page.Controls)
                if (c is LinkLabel link && link.Text == "Change…") PlaceFolderLine(link);
            Rejudge();
        }

        // ------------------------------------------------------------------ installing

        private Label _headline, _detail;
        private NiceProgress _bar;
        private TextBox _logBox;

        private void StartInstall()
        {
            if (_chosen == null || !_chosen.Runnable) return;
            var plan = new InstallPlan
            {
                Engine = _chosen.Engine,
                Folder = _folder,
                DesktopIcon = _desktop?.Checked ?? true,
                ForClaudeCode = _claude?.Checked ?? false,
                Source = _options.Source ?? Installer.DefaultSource,
            };
            ShowProgress(plan);
        }

        private async void ShowProgress(InstallPlan plan)
        {
            var page = NewPage("Setting things up…");
            _headline = Text_(page, "Starting…", Dpi.Px(100), 12.5f, bold: true);
            _bar = new NiceProgress { Location = new Point(Dpi.Px(40), Dpi.Px(140)), Width = _content.Width - Dpi.Px(80) };
            page.Controls.Add(_bar);
            _detail = Text_(page, "", Dpi.Px(160), 9.5f, Palette.Muted);
            Text_(page, "You can leave this running and go back to what you were doing. If anything interrupts it, " +
                        "run it again — it carries on from where it stopped.", Dpi.Px(200), 9.5f, Palette.Muted);

            _logBox = new TextBox
            {
                Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, WordWrap = false,
                Font = new Font("Consolas", 8.5f), BackColor = Color.White, ForeColor = Palette.Ink,
                BorderStyle = BorderStyle.FixedSingle, Visible = false,
                Location = new Point(Dpi.Px(40), Dpi.Px(282)),
                Size = new Size(_content.Width - Dpi.Px(80), Dpi.Px(200)),
            };
            page.Controls.Add(_logBox);
            var details = new LinkLabel
            {
                Text = "Show details", AutoSize = true, LinkColor = Palette.Accent, Font = new Font("Segoe UI", 9f),
                Location = new Point(Dpi.Px(40), Dpi.Px(256)),
            };
            details.LinkClicked += (_, __) =>
            {
                _logBox.Visible = !_logBox.Visible;
                details.Text = _logBox.Visible ? "Hide details" : "Show details";
            };
            page.Controls.Add(details);
            var cancel = Button_(page, "Cancel", false, 0, () => { if (ConfirmStop()) _cancel?.Cancel(); }, 120);

            _installer = new Installer();
            _installer.Stage += (headline, fraction, detail) => OnUi(() =>
            {
                _headline.Text = headline;
                _bar.Value = fraction;
                _detail.Text = detail ?? "";
            });
            _installer.Line += line => OnUi(() =>
            {
                _logBox.AppendText(line + Environment.NewLine);
            });

            _cancel = new CancellationTokenSource();
            _installing = true;
            try
            {
                await _installer.RunAsync(plan, _cancel.Token);
                _installing = false;
                ShowDone(plan);
            }
            catch (OperationCanceledException)
            {
                _installing = false;
                ShowFailed(plan, "Stopped. Nothing is lost — run this again whenever you like and it carries on from where it got to.");
            }
            catch (Exception ex)
            {
                _installing = false;
                ShowFailed(plan, ex is InstallException ? ex.Message : "Something unexpected went wrong: " + ex.Message);
            }
        }

        private void OnUi(Action act)
        {
            if (IsDisposed) return;
            if (InvokeRequired) BeginInvoke(act); else act();
        }

        private bool ConfirmStop() =>
            MessageBox.Show(this, "Stop the install? It can carry on later from where it stopped.", "claude-voice setup",
                            MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes;

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (_installing && e.CloseReason == CloseReason.UserClosing)
            {
                if (!ConfirmStop()) { e.Cancel = true; return; }
                _cancel?.Cancel();
            }
            base.OnFormClosing(e);
        }

        /// <summary>True when the install ended in the failure page -- the --auto exit code.</summary>
        public bool Failed { get; private set; }

        private void ShowDone(InstallPlan plan)
        {
            if (_options.Auto) { Close(); return; }
            var page = NewPage("All set!");
            var y = Dpi.Px(96);
            y = Text_(page, "Abby just said hello — did you hear her? If not, check that your sound is on and try the button below.",
                      y).Bottom + Dpi.Px(16);
            if (ForWhom != null)
                y = Text_(page, $"Now go back to {ForWhom} and open its Voices panel. The characters can speak now.",
                          y, 11.5f, Palette.Accent, bold: true).Bottom + Dpi.Px(16);
            if (plan.Engine.EnglishOnly)
                y = Text_(page, "Breeze reads English. For another language, run this again and pick Qwen — you can have both.",
                          y, 9.5f, Palette.Muted).Bottom + Dpi.Px(10);
            Text_(page, "claude-voice lives in " + plan.Folder +
                        (plan.DesktopIcon ? ". The Abby icon on your desktop opens its panel." : "."),
                  y, 9.5f, Palette.Muted);

            Button_(page, "Finish", true, 0, Close, 120);
            Button_(page, "Open the panel", false, Dpi.Px(130), () => _ = Installer.OpenPanelAsync(), 150);
            Button_(page, "Say hello again", false, Dpi.Px(290), () => _ = Installer.SayHelloAsync(), 150);
        }

        private void ShowFailed(InstallPlan plan, string problem)
        {
            Failed = true;
            if (_options.Auto)
            {
                Console.Error.WriteLine(problem);
                try { File.WriteAllText(Path.Combine(Path.GetTempPath(), "claude-voice-setup.log"), problem + Environment.NewLine + (_installer?.Log ?? "")); } catch { }
                Close();
                return;
            }
            var page = NewPage("That didn't finish");
            var y = Text_(page, problem, Dpi.Px(96), 11f).Bottom + Dpi.Px(14);
            y = Text_(page, "Nothing is lost. Press Try again and it picks up where it stopped — the downloads resume too.",
                      y, 10f, Palette.Muted).Bottom + Dpi.Px(14);
            var help = new LinkLabel
            {
                Text = "What to try when it goes wrong", AutoSize = true, LinkColor = Palette.Accent,
                Location = new Point(Dpi.Px(40), y), Font = new Font("Segoe UI", 9.5f),
            };
            help.LinkClicked += (_, __) => Open("https://github.com/TraxData313/claude-voice/blob/main/docs/troubleshooting.md");
            page.Controls.Add(help);

            Button_(page, "Try again", true, 0, () => ShowProgress(plan), 130);
            Button_(page, "Copy details", false, Dpi.Px(140), () =>
            {
                try { Clipboard.SetText(problem + Environment.NewLine + Environment.NewLine + (_installer?.Log ?? "")); } catch { }
            }, 140);
            Button_(page, "Back", false, Dpi.Px(290), ShowChoose, 100);
        }

        // ------------------------------------------------------------------ bits

        private void ShowForScreenshot(string page)
        {
            var plan = new InstallPlan { Engine = EngineInfo.Qwen, Folder = _folder };
            switch (page)
            {
                case "choose": ShowChoose(); break;
                case "done": ShowDone(plan); break;
                case "fail": ShowFailed(plan, "The install stopped: could not reach huggingface.co."); break;
                case "progress":
                    ShowChoose();
                    var p = NewPage("Setting things up…");
                    Text_(p, "Downloading the voice model…", Dpi.Px(100), 12.5f, bold: true);
                    p.Controls.Add(new NiceProgress { Location = new Point(Dpi.Px(40), Dpi.Px(140)), Width = _content.Width - Dpi.Px(80), Value = 0.42 });
                    Text_(p, "1.0 of 2.4 GB", Dpi.Px(160), 9.5f, Palette.Muted);
                    break;
            }
        }

        private static void Open(string url)
        {
            try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); } catch { }
        }

        private static Image LoadImage(string name)
        {
            try
            {
                var s = typeof(SetupForm).Assembly.GetManifestResourceStream(name);
                return s == null ? null : Image.FromStream(s);
            }
            catch { return null; }
        }

        private static Icon LoadIcon()
        {
            try
            {
                var s = typeof(SetupForm).Assembly.GetManifestResourceStream("abby.ico");
                return s == null ? null : new Icon(s);
            }
            catch { return null; }
        }
    }
}
