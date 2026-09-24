using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.Windows.Forms;

namespace ClaudeVoiceSetup
{
    /// <summary>The colours, taken from the painting on the left so the window reads as one picture.</summary>
    internal static class Palette
    {
        public static readonly Color Paper = Color.FromArgb(251, 248, 241);
        public static readonly Color Ink = Color.FromArgb(46, 38, 32);
        public static readonly Color Muted = Color.FromArgb(117, 105, 94);
        public static readonly Color Line = Color.FromArgb(228, 220, 203);
        public static readonly Color Card = Color.White;
        public static readonly Color CardDim = Color.FromArgb(243, 240, 234);
        public static readonly Color Accent = Color.FromArgb(181, 101, 62);
        public static readonly Color AccentDark = Color.FromArgb(150, 80, 46);
        public static readonly Color AccentSoft = Color.FromArgb(250, 236, 226);
        public static readonly Color Good = Color.FromArgb(104, 132, 58);
        public static readonly Color Warn = Color.FromArgb(176, 120, 30);
        public static readonly Color Bad = Color.FromArgb(160, 72, 60);
    }

    internal static class Dpi
    {
        public static float Scale = 1f;
        public static int Px(float v) => (int)Math.Round(v * Scale);

        public static GraphicsPath Rounded(RectangleF r, float radius)
        {
            var d = radius * 2;
            var path = new GraphicsPath();
            path.AddArc(r.X, r.Y, d, d, 180, 90);
            path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            path.CloseFigure();
            return path;
        }
    }

    /// <summary>A flat, rounded button: filled for the thing to do next, outlined for the rest.</summary>
    internal sealed class NiceButton : Control
    {
        public bool Primary { get; set; }
        private bool _hover, _down;

        public NiceButton(string text, bool primary)
        {
            Text = text;
            Primary = primary;
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.UserPaint | ControlStyles.ResizeRedraw | ControlStyles.SupportsTransparentBackColor, true);
            BackColor = Color.Transparent;
            Cursor = Cursors.Hand;
            Font = new Font("Segoe UI Semibold", 10f);
            Size = new Size(Dpi.Px(140), Dpi.Px(40));
            TabStop = true;
        }

        protected override void OnMouseEnter(EventArgs e) { _hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { _hover = false; _down = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnMouseDown(MouseEventArgs e) { _down = true; Invalidate(); base.OnMouseDown(e); }
        protected override void OnMouseUp(MouseEventArgs e) { _down = false; Invalidate(); base.OnMouseUp(e); }
        protected override void OnEnabledChanged(EventArgs e) { Invalidate(); base.OnEnabledChanged(e); }
        protected override void OnTextChanged(EventArgs e) { Invalidate(); base.OnTextChanged(e); }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Enter || e.KeyCode == Keys.Space) OnClick(EventArgs.Empty);
            base.OnKeyDown(e);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
            var r = new RectangleF(0.5f, 0.5f, Width - 1.5f, Height - 1.5f);
            using (var path = Dpi.Rounded(r, Dpi.Px(8)))
            {
                Color fill, border, text;
                if (!Enabled) { fill = Palette.CardDim; border = Palette.Line; text = Palette.Muted; }
                else if (Primary) { fill = _down || _hover ? Palette.AccentDark : Palette.Accent; border = fill; text = Color.White; }
                else { fill = _hover ? Palette.AccentSoft : Palette.Card; border = Palette.Line; text = Palette.Ink; }
                using (var b = new SolidBrush(fill)) g.FillPath(b, path);
                using (var p = new Pen(border, 1f)) g.DrawPath(p, path);
                TextRenderer.DrawText(g, Text, Font, Rectangle.Round(r), text,
                    TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
            }
            if (Focused && ShowFocusCues)
                using (var p = new Pen(Palette.AccentDark, 1f) { DashStyle = DashStyle.Dot })
                    g.DrawRectangle(p, 3, 3, Width - 7, Height - 7);
        }
    }

    /// <summary>One engine to choose, drawn as a card: its name, what it is like, what it needs, and
    /// whether this machine can run it.</summary>
    internal sealed class EngineCard : Control
    {
        public readonly EngineInfo Engine;
        public Verdict Verdict = new Verdict();
        public bool Recommended;
        public bool LanguageMismatch;
        private bool _selected, _hover;

        public event Action<EngineCard> Picked;

        public EngineCard(EngineInfo engine)
        {
            Engine = engine;
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.UserPaint | ControlStyles.ResizeRedraw | ControlStyles.Selectable, true);
            Cursor = Cursors.Hand;
            Height = Dpi.Px(82);
            TabStop = true;
        }

        public bool Selected
        {
            get => _selected;
            set { _selected = value; Invalidate(); }
        }

        public bool Runnable => Verdict.Fit != Fit.No;

        protected override void OnMouseEnter(EventArgs e) { _hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { _hover = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnGotFocus(EventArgs e) { Invalidate(); base.OnGotFocus(e); }
        protected override void OnLostFocus(EventArgs e) { Invalidate(); base.OnLostFocus(e); }

        protected override void OnClick(EventArgs e)
        {
            if (Runnable) Picked?.Invoke(this);
            base.OnClick(e);
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Space || e.KeyCode == Keys.Enter) OnClick(EventArgs.Empty);
            base.OnKeyDown(e);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
            g.Clear(Parent?.BackColor ?? Palette.Paper);

            var r = new RectangleF(1, 1, Width - 3, Height - 3);
            var runnable = Runnable;
            using (var path = Dpi.Rounded(r, Dpi.Px(10)))
            {
                var fill = !runnable ? Palette.CardDim : _selected ? Palette.AccentSoft : Palette.Card;
                var border = _selected ? Palette.Accent : _hover && runnable ? Palette.Muted : Palette.Line;
                using (var b = new SolidBrush(fill)) g.FillPath(b, path);
                using (var p = new Pen(border, _selected ? 2f : 1f)) g.DrawPath(p, path);
            }

            // The round tick on the left: empty, or filled when chosen.
            var dot = new RectangleF(Dpi.Px(16), Dpi.Px(14), Dpi.Px(18), Dpi.Px(18));
            using (var p = new Pen(runnable ? (_selected ? Palette.Accent : Palette.Muted) : Palette.Line, 1.6f))
                g.DrawEllipse(p, dot);
            if (_selected)
                using (var b = new SolidBrush(Palette.Accent))
                    g.FillEllipse(b, RectangleF.Inflate(dot, -Dpi.Px(4), -Dpi.Px(4)));

            var left = Dpi.Px(46);
            var width = Width - left - Dpi.Px(14);
            var ink = runnable ? Palette.Ink : Palette.Muted;
            using (var title = new Font("Segoe UI Semibold", 11.5f))
            using (var body = new Font("Segoe UI", 9f))
            using (var small = new Font("Segoe UI", 8.5f))
            using (var badgeFont = new Font("Segoe UI Semibold", 8f))
            {
                TextRenderer.DrawText(g, Engine.Title, title, new Point(left, Dpi.Px(10)), ink, TextFormatFlags.NoPadding);

                // The badge after the title: recommended, slower, or why not.
                var titleWidth = TextRenderer.MeasureText(g, Engine.Title, title, Size.Empty, TextFormatFlags.NoPadding).Width;
                string badge = null;
                var badgeColor = Palette.Good;
                if (!runnable) { badge = "won't run here"; badgeColor = Palette.Bad; }
                else if (LanguageMismatch) { badge = "not your language"; badgeColor = Palette.Warn; }
                else if (Recommended) { badge = "recommended"; }
                else if (Verdict.Fit == Fit.Slow) { badge = "may be slow"; badgeColor = Palette.Warn; }
                if (badge != null)
                {
                    var size = TextRenderer.MeasureText(g, badge, badgeFont, Size.Empty, TextFormatFlags.NoPadding);
                    var br = new RectangleF(left + titleWidth + Dpi.Px(10), Dpi.Px(12), size.Width + Dpi.Px(14), size.Height + Dpi.Px(4));
                    using (var path = Dpi.Rounded(br, br.Height / 2))
                    using (var b = new SolidBrush(Color.FromArgb(34, badgeColor)))
                        g.FillPath(b, path);
                    TextRenderer.DrawText(g, badge, badgeFont, Rectangle.Round(br), badgeColor,
                        TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPadding);
                }

                TextRenderer.DrawText(g, Engine.Tagline, body, new Rectangle(left, Dpi.Px(34), width, Dpi.Px(20)), ink,
                    TextFormatFlags.EndEllipsis | TextFormatFlags.NoPadding);

                var facts = runnable && string.IsNullOrEmpty(Verdict.Why)
                    ? $"{Engine.Needs}  ·  {Engine.Languages}  ·  ~{Engine.DownloadGb:0.#} GB download"
                    : (Verdict.Why.Length > 0 ? Verdict.Why : Engine.Needs);
                var factColor = runnable && Verdict.Fit != Fit.Slow ? Palette.Muted : (runnable ? Palette.Warn : Palette.Bad);
                TextRenderer.DrawText(g, facts, small, new Rectangle(left, Dpi.Px(55), width, Dpi.Px(20)), factColor,
                    TextFormatFlags.EndEllipsis | TextFormatFlags.NoPadding);
            }

            if (Focused && ShowFocusCues)
                using (var p = new Pen(Palette.AccentDark, 1f) { DashStyle = DashStyle.Dot })
                    g.DrawRectangle(p, 4, 4, Width - 9, Height - 9);
        }
    }

    /// <summary>A thin, rounded progress bar that can also just say "working" with a moving sheen.</summary>
    internal sealed class NiceProgress : Control
    {
        private double? _value;
        private float _sheen;
        private readonly Timer _timer = new Timer { Interval = 30 };

        public NiceProgress()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true);
            Height = Dpi.Px(10);
            _timer.Tick += (_, __) => { _sheen = (_sheen + 0.012f) % 1.4f; Invalidate(); };
            _timer.Start();
        }

        public double? Value
        {
            get => _value;
            set { _value = value.HasValue ? Math.Max(0, Math.Min(1, value.Value)) : (double?)null; Invalidate(); }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) _timer.Dispose();
            base.Dispose(disposing);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.Clear(Parent?.BackColor ?? Palette.Paper);
            var r = new RectangleF(0, 0, Width - 1, Height - 1);
            using (var track = Dpi.Rounded(r, r.Height / 2))
            using (var b = new SolidBrush(Palette.Line))
                g.FillPath(b, track);

            if (_value.HasValue)
            {
                var w = Math.Max(r.Height, (float)(r.Width * _value.Value));
                using (var fill = Dpi.Rounded(new RectangleF(0, 0, w, r.Height), r.Height / 2))
                using (var b = new SolidBrush(Palette.Accent))
                    g.FillPath(b, fill);
            }
            else
            {
                var w = r.Width * 0.3f;
                var x = (_sheen - 0.3f) * r.Width;
                var seg = RectangleF.Intersect(new RectangleF(x, 0, w, r.Height), r);
                if (seg.Width > 1)
                    using (var fill = Dpi.Rounded(seg, Math.Min(seg.Width, r.Height) / 2))
                    using (var b = new SolidBrush(Palette.Accent))
                        g.FillPath(b, fill);
            }
        }
    }
}
