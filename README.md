# Write or Die

A modern Markdown editor with a **dangerous-writing mode**. Start a session and
keep typing — if you stop, the text blurs; stay idle too long and *everything is
deleted*. Survive until the session timer ends and your text is auto-copied to
the clipboard.

## Disclaimer

This project is 100% vibe coded. There should be no expectations of maintenance.

## Run

```bash
pip install -r requirements.txt
python main.py
```

## Build desktop apps

Install the build requirements first:

```bash
pip install -r requirements-build.txt
```

PyInstaller does not cross-compile. Run the build on the OS you want to target:

```bash
# Windows: dist/WriteOrDie.exe
python build_app.py --target windows

# macOS: dist/WriteOrDie.app
python build_app.py --target macos

# Debian-based Linux: dist/WriteOrDie plus write-or-die_0.1.0_amd64.deb
python build_app.py --target linux
```

`python build_exe.py` remains as a Windows-only shortcut.

## How it works

1. Pick a **Mode** from the Session menu and press **Start session**.
2. Keep writing. When you pause, the text starts to **blur**.
3. A red **deletion countdown** shows how long until the wipe. Only text written
   in the current session is faded/highlighted as at-risk. Type to reset it.
4. Stay idle past the threshold and the editor is **cleared**.
5. Survive until the session timer hits `00:00` and your text is **copied to the
   clipboard** with a popup confirmation.

## Modes

| Mode     | Idle → blur | Idle → delete | Session |
|----------|-------------|---------------|---------|
| Gentle   | 5 s         | 10 s          | 5 min   |
| Standard | 3 s         | 7 s           | 10 min  |
| Hardcore | 2 s         | 5 s           | 15 min  |
| Custom   | configurable (saved between sessions) |

Only the text written **since the current session started** is at risk — text
from earlier sessions is protected and never wiped.

## Session history

Use **Session → History...** to view the persistent master session log. Sessions
are grouped by date and time, color-coded by outcome/deletions, and include mode,
word count, deletions, WPM, average seconds per word, and duration.

Use **Session → Disable Stop during session** to prevent stopping a running
session manually. This is enabled automatically when Hardcore mode is selected.

## View menu

- **Hide all text** — blind-writing mode (you type but can't see the text). The
  text reappears (and is copied) when the session ends.
- **Focus mode** — hides everything except the text right behind the cursor.
  Use the in-menu **+/- counters** to set how many *words* and/or *sentences* to
  reveal (the larger span wins; set a counter to 0 to disable it). With
  sentences = 1, the current sentence disappears once you finish it with `.!?`.
- **Hemingway mode** — disables backspace, delete, and cursor movement: you can
  only write forward.
- **Typewriter mode** — keeps the current line vertically centered while text
  flows upward as you type.
- **Show timer** — the session countdown.
- **Show deletion countdown** — the red idle-to-deletion counter.
- **Show session marks** — ephemeral dashed lines marking where each session
  started/ended (drawn only; never written into the `.md` file).
- **Dark mode** — toggle between dark and light color themes.

## Format menu

- **Font** — choose any installed font family from the menu dropdown.
- **Font size** — +/- counter (6–48 pt).

All View/Format settings and your Custom timing values persist between sessions.

## Interface details

- **Auto-hiding scrollbar** — the editor's scrollbar is thin and minimal, fading
  in while you scroll or move the mouse and fading out after about a second idle.
- **Focus reveal animation** — in Focus mode, newly revealed text fades in and a
  finished sentence fades out instead of snapping.
- **Session stats** — the status bar shows live per-session stats on the left
  (words, deletions, average seconds-per-word, and WPM), with the total document
  word count on the right. When no session is running it shows the last session's
  summary.

## Files

- `main.py` — the entire application (PySide6 / Qt6).
- `requirements.txt` — the single dependency.
