# Plan: a settings window, a chip for the engine, start with Windows, and a memory readout

> **Built, and three quarters of it shipped in 1.5.0.** This is the plan as Toni and Abby wrote
> it, kept because it records what was decided and why while none of it existed yet. What
> actually happened is at the end of [panel-plan.md](panel-plan.md), under *What changed in the
> building* — read that before changing anything here. Two things to know first:
>
> - **Section 4, the memory readout, is not in the product.** It was built and measured and
>   then removed at Toni's word, before the release: he did not want the number. It is written
>   up with its working code at [memory-readout.md](memory-readout.md), so it can go back in as
>   a paste. That page also carries the two findings this one is wrong about — `nvidia-smi`
>   cannot report per-process VRAM on a consumer Windows machine at all, and committed RAM is
>   not the "about 3.5 GB" the engine switch is described in terms of; that 3.5 GB is the card.
> - **The top strip was never what set the window's minimum width**, so the drop expected below
>   did not happen: 348 before, 348 after. The header sets it.
>
> The one instruction here that was not followed is the last line of the next paragraph: this
> file was moved and kept rather than deleted.

Planned by Toni and Abby on 2026-08-18. The decisions below are **made** — build them,
don't re-litigate them. When the work is done: fold what is worth keeping into
`docs/panel-plan.md` (the design log, in its voice) and `CHANGELOG.md`, then delete this
file.

Read `CLAUDE.md` and skim `docs/panel-plan.md` before touching `panel.py` — and match the
comment voice: full sentences that say *why*, not captions. No new dependencies. Files are
LF. Anything you spawn should name `C:\Users\Trax\miniconda3\python.exe` in full — spawned
processes do not get the shell profile, so bare `python` is not on their PATH.

## Where things stand

The working tree already carries **finished, uncommitted** work from earlier the same day
(`panel.py`, `voice_lib.py`, `README.md`, `docs/panel-plan.md`). Build on it, don't redo it:

- An `auto start` tick in the top strip (`panelAutostart` in the config): on the first
  poll answer, the panel loads an engine if none is running and turns the voice on. See
  `open_up` / `turn_voice_on` in `panel.py` — the voice is turned on by *writing the
  config*, not posting, because the engine being started is not up yet to be posted to.
- The two coloured `tk.Button` switches (engine, stop/play) copy their size off the themed
  skip button — `_match_switches`, called from `apply_theme`, and it **cannot** be called
  from `_build` (settling the layout mid-build delivers resize events to a half-built
  window; the comments explain).

Committing is Toni's call; leave git alone unless he says.

## 1. The settings button and dialog

A settings button in the **top right of the strip**, where the tick boxes are now. A plain
`ttk.Button` in the `Icon.TButton` style (like the skip buttons — it is not a state switch,
so it is not one of the coloured pair), wearing the cog `_gear()` returns, with a `Tip`
saying "settings". It opens a dialog.

The dialog: model it on `open_typer`/`close_typer` — one instance at a time, `Esc` closes,
positioned near the window, and repainted on theme flips the way `_paint_typer` is. Use
`self.check()` for tick boxes (it handles both themes) and note that `apply_theme`'s
`_descendants(self.root)` walk already reaches widgets in a `Toplevel`, because a Toplevel
is a child of root.

Every setting is a row: the tick with its name, and a small grey **description** under it
(`FONT_SMALL`, wraplength ~300). Group with the `_section` helper:

- **when it opens**
  - `auto start` (existing `panelAutostart`, tick moves here from the strip) — "when this
    window opens, load the engine and turn the voice on. Not about Windows starting —
    that is the tick below."
  - `start with Windows` — see task 3.
- **the window**
  - `dark` (`panelDark`) and `on top` (`panelTopmost`) — both move here from the strip,
    same commands they have now.
- **updates**
  - the `auto update` tick (`updateCheck`) and the **check now** button move here from the
    footer, with the existing description voice ("the one thing here that touches the
    network"). The footer's version *label* stays where it is — its colour is the
    update-available beacon and must remain visible with the dialog closed.

Gotcha, and it is a real one: `drain_update` configures `update_btn` from a background
answer. Once that button lives in a dialog, the dialog may be **closed** when the answer
lands — guard with `winfo_exists`/`TclError`, keep the message on `self.update_msg` (it
already exists), and have the dialog paint from it when opened. The typer has a comment
about exactly this class of bug.

After the move the strip holds only the settings button. Rewrite the strip's "left of it
is deliberately empty" comment honestly (it grew a Settings, on the right), re-measure the
window's minimum width (it will drop — the strip was 190 wide with three ticks), and put
the new numbers in the design-log entry.

Copy above is draft — polish into the repo's voice, keep the meaning.

## 2. The engine button wears a chip

Decided: the engine button's icon is the **chip**, `U+E950` in Segoe MDL2 Assets
(rendered and chosen by eye over power/bolt/robot). The gear moves to the settings button,
where a gear means what everyone thinks it means.

- Add `MDL2_CHIP = "\ue950"` beside `MDL2_GEAR`; the engine button takes it when the MDL2
  font is present (`gear_ok` — consider renaming to `mdl2_ok`, since it now covers two
  glyphs) and falls back to `GLYPH["engine"]` words/wheel exactly as today.
- The gear had a one-point size bump ("reads as the runt of the row") — check by
  screenshot whether the chip needs it; the button's *box* no longer depends on the glyph
  (`_match_switches` owns it), so this is purely how the glyph fills it.

## 3. Start with Windows

A tick in "when it opens": opening this window when Toni logs in. Implementation:

- **No config key.** Truth is the filesystem: does
  `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Abby for Claude.lnk` exist.
  The tick reads existence on dialog open; ticking creates the shortcut, unticking deletes
  it, and the handler re-reads existence afterwards so a failed create cannot leave the
  box lying.
- Read `make_shortcut.ps1` and mirror what it does for the Desktop shortcut (pythonw, the
  icon, the working directory). Create the .lnk the same way — a spawned PowerShell
  one-liner with `WScript.Shell` COM is fine, `CREATE_NO_WINDOW`.
- The two ticks compose on purpose: `start with Windows` opens the panel at login, and if
  `auto start` is also ticked the panel then loads the engine and turns the voice on.
  Both ticked is the "it just talks" mode. Say so in the description.

## 4. Memory next to "engine: ready"

Decided: **live-but-calm memory, no utilization meter.** Memory is flat except during load
and unload — exactly the moments worth watching — so a live number behaves like a static
one, and you get to watch it climb as the model loads (`/state` answers during a load, so
this works).

Server side (`speak_server.py`):

- `/state` gains `"memory": {"ram": <bytes>, "vram": <bytes or null>}`.
- RAM: the server's own process, via ctypes `psapi.GetProcessMemoryInfo` with
  `PROCESS_MEMORY_COUNTERS_EX`, reporting **PrivateUsage** (committed). Committed is the
  "hand back 3.5 GB" number the whole cog story is told in; working set is not. Cheap
  enough to call inline per request.
- VRAM: a background thread samples every ~10 s:
  `nvidia-smi --query-compute-apps=pid,used_gpu_memory --format=csv,noheader,nounits`
  (`CREATE_NO_WINDOW`), find our own PID, units are MiB. Cache the answer; `/state` serves
  the cache. If the binary is missing or it errors, mark it unavailable and stop asking —
  an engine restart is the retry. PID absent from the list is a valid answer (model not on
  the card): null, RAM only.

Panel side:

- Footer appends the **sum of what is known**, one decimal: `engine: ready · 5.4 GB`.
  Terse on purpose — below ~396 px the footer is the thing that truncates.
- A `Tip` on the status label gives the split, as a callable (it changes):
  "3.3 GB in RAM + 2.1 GB on the graphics card", or just "3.3 GB in RAM" when vram is
  null. `Tip` binds fine to a label.
- An engine too old to send `"memory"` gets nothing shown — same lesson as volume:
  never invent a number for a process that did not report one.

## Verification (do these, not a subset)

The technique from the planning session: a Tk script run with the full Miniconda path,
screenshots via Pillow `ImageGrab`, engine likely already up on port 8765 — do not kill it.

- All four transport buttons equal sizes in **both** themes (was 37×29 dark, 39×31 light).
- Screenshots: strip + dialog, dark and light. Flip the theme **while the dialog is open**:
  colours follow, no TclError.
- `check now` inside the dialog; close the dialog before the answer lands: no crash, and
  reopening shows the result.
- `start with Windows`: tick creates the .lnk, untick removes it, nothing left behind.
- With the engine up: footer shows the number, tooltip shows the split.
- The auto-start-on-open flow still works (stub `start_engine`/`turn_voice_on` and drive
  `open_up` with None / enabled-false / enabled-true).
- A narrow window: the footer note is still the only thing that gives.

## Docs and release

- `README.md`: the auto-start bullet just added will need reshaping into a settings-window
  bullet; add the memory readout. `docs/panel.png` is stale (still shows mismatched
  buttons) — retake it once the UI settles: dark theme, engine up, same crop.
- `docs/panel-plan.md`: append the story in its voice — what, why, what it cost, with
  measured numbers.
- Release **1.5.0** covering everything since 1.4.1 (auto start, equal buttons, settings
  window, chip, start with Windows, memory readout): `CHANGELOG.md` + `version.json`
  together, headline written for the ear, `needsSetup` false (no hook or command changes).
  The version also lives in `.claude-plugin/plugin.json` and the marketplace entry —
  `claude plugin tag` checks they agree.
