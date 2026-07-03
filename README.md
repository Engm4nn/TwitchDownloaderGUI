# TwitchDownloader GUI

> A fork of [**lay295/TwitchDownloader**](https://github.com/lay295/TwitchDownloader)
> that adds a simple, self-contained **graphical interface** on top of the
> official command-line tool.

The upstream project ships a powerful cross-platform CLI
(`TwitchDownloaderCLI`) for downloading Twitch VODs, clips, and chat, and for
rendering chat to video. This fork keeps all of that untouched and adds a small
local web GUI so you don't have to remember command-line flags — you just open a
page in your browser and click.

Everything the CLI does is still done by the CLI. The GUI is a thin wrapper that
builds and runs the right command for you.

---

## What this fork adds

Everything lives in the [`gui/`](gui/) folder — the rest of the repository is
the unmodified upstream source.

- **`gui/twitchdownloader_gui.py`** — the whole GUI in a single file, using only
  the Python standard library (no `pip install` needed).
- **`gui/TwitchDownloader GUI.command`** — a double-click launcher for macOS.
- **`gui/README.md`** — usage notes for the GUI on its own.

The GUI provides five tabs:

| Tab | What it does |
|---|---|
| **VOD** | Download a VOD/highlight as `.mp4` (or `.m4a` audio only), with optional trim, quality, and OAuth for sub-only VODs |
| **Clip** | Download a clip |
| **Chat** | Download chat as JSON (renderable), HTML, or plain text |
| **Chat Render** | Render a downloaded chat JSON to a video overlay, with a font picker, font size, dimensions, framerate, background color, outlines, and timestamps |
| **Info** | Show available qualities and metadata for a VOD or clip |

Quality-of-life touches: paste a full Twitch URL *or* a bare ID (both work),
filenames are auto-suggested, live progress and logs stream at the bottom of the
page, output-file collisions auto-rename instead of blocking, and your output
folder / OAuth token are remembered between sessions.

---

## Quick start (macOS, Apple Silicon)

The easiest way is the prebuilt bundle:

1. Go to the [**Releases**](https://github.com/Engm4nn/TwitchDownloaderGUI/releases)
   page and download the latest `TwitchDownloaderGUI-macos-arm64.zip`.
2. Unzip it.
3. Double-click **`TwitchDownloader GUI.command`**.
   - On first launch macOS may block it (unsigned). Right-click the file →
     **Open**, or run `xattr -dr com.apple.quarantine .` inside the folder.
4. A browser tab opens at `http://127.0.0.1:5959` (localhost only). Keep the
   terminal window open while you use it; press Ctrl+C to quit.

The bundle already contains a matching `TwitchDownloaderCLI` binary, so there is
nothing else to download.

### Running from source instead

If you already have a `TwitchDownloaderCLI` binary, you can just run the GUI
script and it will find the CLI automatically (next to the script, one folder
up, or on your `PATH`):

```sh
python3 gui/twitchdownloader_gui.py
```

### Requirements

- **Python 3** — preinstalled on macOS (or `brew install python`).
- **ffmpeg** — `brew install ffmpeg`. Needed for VOD downloads and chat renders.

---

## Building / getting the CLI yourself

This fork does **not** modify the downloader itself, so you don't need to build
anything to use the GUI. If you want a fresh or different-platform
`TwitchDownloaderCLI`, grab an official build from the upstream
[releases](https://github.com/lay295/TwitchDownloader/releases), or build it from
the source in this repo per the upstream instructions in
[`README-upstream.md`](README-upstream.md).

The prebuilt bundle in this fork's releases is **macOS Apple Silicon (arm64)**
only. For Intel Macs, Windows, or Linux, use the matching upstream CLI binary
alongside `gui/twitchdownloader_gui.py`.

---

## Credits & license

All the real work — downloading, parsing, and rendering — is done by
[**lay295/TwitchDownloader**](https://github.com/lay295/TwitchDownloader) and its
contributors. This fork only adds a convenience GUI on top.

The project is licensed under the **MIT License** (see
[`LICENSE.txt`](LICENSE.txt)); this fork's additions are released under the same
license. The original upstream documentation is preserved in
[`README-upstream.md`](README-upstream.md).
