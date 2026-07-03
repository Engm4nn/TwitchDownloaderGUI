# TwitchDownloader GUI

A tiny, zero-dependency local web GUI for [TwitchDownloaderCLI](https://github.com/lay295/TwitchDownloader).
It wraps the CLI's main functions — download VODs, clips, and chat, render chat
to video, and look up VOD/clip info — behind a simple browser UI, on macOS.

Only requirements: **Python 3** (preinstalled on macOS / `brew install python`)
and **ffmpeg** (`brew install ffmpeg`) for VOD downloads and chat renders.

## Quick start

1. Download the release bundle (see the repo's Releases page) and unzip it,
   **or** put `twitchdownloader_gui.py` next to a `TwitchDownloaderCLI` binary.
2. Double-click **`TwitchDownloader GUI.command`**, or run:

   ```
   python3 twitchdownloader_gui.py
   ```

3. A browser tab opens at `http://127.0.0.1:5959` (localhost only). Keep the
   terminal window open while you use it; press Ctrl+C to quit.

The script finds `TwitchDownloaderCLI` automatically if it sits next to the
script, one folder up, or anywhere on your `PATH`.

## What each tab does

| Tab | Function |
|---|---|
| **VOD** | Download a VOD/highlight as `.mp4` (or `.m4a` audio only), with optional trim, quality, and OAuth for sub-only VODs |
| **Clip** | Download a clip |
| **Chat** | Download chat as JSON (renderable), HTML, or plain text |
| **Chat Render** | Render a downloaded chat JSON to a video overlay |
| **Info** | Show available qualities and metadata for a VOD or clip |

## Notes

- Paste a full Twitch URL **or** a bare ID — both work. Filenames auto-suggest.
- One job runs at a time; live progress and log stream at the bottom of the page.
- Output-file collisions auto-rename, so the CLI never blocks on a prompt.
- Your output folder and OAuth token are remembered (in your browser's local storage).
- Numeric render settings (width, height, framerate, font size) are whole numbers.
- Chat Render has a **font picker** — "Inter Embedded" (bundled) is the default; other fonts installed on the Mac are also listed. Default font size is 24.
- **Chat downloads embed emotes/badges by default** (BTTV / FFZ / 7TV, each toggleable). Embedding bakes the emotes into the JSON so they always render — this is why a chat downloaded with embedding shows third-party emotes and one without does not.
- Chat Render also has BTTV / FFZ / 7TV toggles and a **font picker** ("Inter Embedded" bundled default, plus fonts installed on the Mac). Default font size is 24.
- Typical chat-overlay workflow: **Chat** tab (JSON, embed on) → **Chat Render** tab.

## Credits

The heavy lifting is done by [lay295/TwitchDownloader](https://github.com/lay295/TwitchDownloader).
This GUI is just a thin local wrapper around its CLI.
