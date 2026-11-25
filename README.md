# Three-Pass Reader

Local paper-reading workspace: split-screen PDF + evidence cards, built around the
three-pass method (Keshav 5-Cs → figure-level evidence → critique). Your answers are
the data — the app never fills anything in.

## Install from a clone

```bash
git clone https://github.com/EnduringChung/three-pass-reader.git
cd three-pass-reader
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows;  source .venv/bin/activate on macOS/Linux
.venv/Scripts/python launcher.py --serve          # then open http://127.0.0.1:8765
```

On first run the app writes `config.json` from the defaults and creates `data/` next to the code —
point `roots` at the folders holding your PDFs (or use **Import → add folder** in the UI).

Options: `--stop`, `--status`, `--restart`, `--install-autostart`.
Windows users can instead double-click `run.bat`, or build the desktop shortcut with
`python make_icon.py` (writes `app.ico`) and point a shortcut at `launcher.py`.

Tests: `.venv/Scripts/python -m pytest tests -q`

## What is NOT in this repo

Your library. `config.json`, `data/` (database, drafts, figure captures), `backups/`,
`exports/` and `logs/` are git-ignored — they hold absolute paths and your own notes.
A clone starts empty; `backup.py` snapshots the database plus draft images.

## Run

Double-click `run.bat` → opens http://127.0.0.1:8765

Reachable from another device on your LAN or VPN: `http://<this-host-ip>:8765`.

## Workflow

1. **Library** (`/`) — auto-scans configured folders. *Import* button: rescan,
   add any folder path, or drag-and-drop PDFs (uploads land in
   `<import_dir>/`).
2. **Read** (`/reader?work=<id>`) — PDF left, notes right. The ⇄ button opens the layout
   menu: PDF left / notes left / PDF top / notes top (remembered per paper); drag the
   divider to resize, panes scroll independently, positions remembered per paper.
   Zoom: `−` / `+` buttons, `0` (or the % chip) = fit width, `+`/`-`/`0` keys,
   Ctrl+scroll or trackpad pinch. Zoom holds your place on the page and is remembered.
   On phones: Paper/Notes tabs at the bottom, zoom in the same bottom strip.
3. **Pass 1** — is this worth more time? (Category, Contributions, then a verdict:
   *advance / park / skip* — click the active one again to clear it). The verdict then
   shows as a chip in the library. Full 5 Cs one tap away.
4. **Pass 2** — evidence cards. ✂ Snapshot (or press `s`), drag a rectangle over a
   figure → it attaches to the current card. Write your observation BEFORE reading the
   authors' claim. Park questions as cards. 🔗 Link cards to each other (Zettelkasten,
   works across papers).
5. **Pass 3** — resolve open cards (resolution is kept separately from your original
   interpretation), 4-point critique, what-ifs.
6. **Highlight** — select text in the PDF and colour it: `1` amber = claim/gap,
   `2` green = evidence, `3` blue = method, `4` red = doubt/overclaim, `5` violet =
   jargon → parking lot. Click a band to recolour, delete, or copy the quote. Works on
   a tablet too (long-press to select; the palette is finger-sized there).
7. **Search** (`/search`) — full-text over your cards; results deep-link back to the
   exact figure.
8. **Export** (⤒ in the reader) — markdown to `<exports>/notes/<slug>.md` with copied
   figure images, plus a row appended to a dated **copy** of your literature matrix in
   `<exports>/`. The original workbook is only ever read; cells the app has nothing for
   are left blank rather than guessed.


## References

Each paper gets a **Refs** tab (next to Scratch).

- **Extract references** — reads the bibliography, one row per reference, with an honest status:
  `exact` (title matches a work in your library), `tentative`, `unmatched`, `unparsed`.
  Three formats are handled: `[1] …`, `1. …` / `1 . …`, and author–year lists.
- **DOIs** — where the publisher printed one it is captured and looked up (Crossref, then DataCite,
  cached), so title/authors/year come from the registry instead of a guess. Entries with a title but
  no DOI get one bibliographic query; a tie between two candidates is stored as `tie:` and left
  `tentative` rather than guessed.
- **Scanned PDFs** — a file with no text layer is flagged `image-only` and extraction is disabled;
  paste the reference list (one per line) or paste DOIs instead. Manual rows carry a `manual` badge,
  are never modified by a later extraction, and win over a parsed row on the same key.
- **Star / why / fix** are yours and are never overwritten by re-extraction.
- **Common references** (`/refs`) ranks references by how many *distinct* papers cite them.

`text_layer` is decided from characters-per-page (≥200 = text), stored per file.


## Writing

`/write` (also the **Write** tab in the reader) is a document editor for prose.

- Headings H1–H3, bold / italic / underline, bullet and numbered lists, blockquote, divider, links,
  undo/redo. Formatting is stored as sanitised HTML (`writerdoc.py`), never as raw browser output.
- **Scratch pad** (reader's Scratch tab) is the same rich-text editor as `/write`: headings, lists,
  links, images, citations, checks, autosave — one pad per paper, stored as a draft with `kind='scratch'`
  so it stays out of the writing list. An old plain-text scratchpad is converted and carried over the
  first time the tab is opened.
- **Import Word**: `⬆ .docx` creates a new document from a Word file — headings (Title / Heading 1–3),
  bold/italic/underline/strike runs, nested bullet and numbered lists (Word's `ilvl` depth is
  preserved), blockquotes, code blocks, tables and inline images (copied into the draft's own folder).
  Unknown styles degrade to plain paragraphs, never to dropped text. The document remembers the file
  name it came from, and **deleting it removes only the writer's copy** — the `.docx` on disk is never
  opened for writing, moved or removed. Old binary `.doc` is refused with
  a message; save as `.docx` first. 25 MB limit.
- **Images** upload to `data/drafts/<id>/` and insert as `<figure><img><figcaption>`; the caption is
  focused on insert. 8 MB limit, PNG/JPEG/WebP/GIF only.
- **Links**: `🔗 Link` links the selected words (a URL without a scheme gets `https://`), and pressing it
  with the cursor inside a link edits or removes it. A contenteditable swallows clicks on `<a>`, so
  **Ctrl+click (Cmd on Mac) opens the link in a new tab** while a plain click still places the caret;
  `↗ Open` opens the link at the cursor, for devices without a Ctrl key.
- **Citations**: `Cite` searches your extracted references and inserts a marker
  (`<cite data-ref data-work>`), not dead text. The style toggle switches the whole document between
  author–year and `[1]` numbering; `Bibliography` appends a reference list generated from the markers
  (regenerating replaces it rather than duplicating).
- **Check** flags grammar/style problems — long sentences, passive voice, hedging, weasel words,
  repeated words, adverb density, inconsistent spelling of the same term. It says *what* is flagged,
  never what to write. Nothing here generates your prose.
- `⤒ Export md` writes `exports/writing/<title>.md` with headings, lists, images, captions and citations.
- Autosave is the same queued-save as the notes pane: 700 ms debounce, flush on blur/tab-hide, and a
  `pagehide` keepalive write, so the last sentence survives a refresh.


## Starting it

- **Starts by itself at logon.** `launcher.py --install-autostart` writes an `HKCU\...\Run` entry that
  runs `pythonw launcher.py --serve` — windowless, no admin, outside any shell. Task Scheduler would be
  tidier but creating a task is denied on this machine (needs elevation); the Run key is not.
- **Favourites**: the `☆` left of every title in the library stars a paper (`PATCH /api/works/{id}/star`,
  stored in `works.starred`). The star and the title turn gold, and `☆ Starred` in the toolbar shows only
  favourites. The reader has the same star in its top bar, so a paper can be favourited while reading.
- **Desktop icon**: `Three-Pass Reader` on the Desktop (created from `launcher.py`, icon `app.ico`).
  Click it and the server starts with no console window, waits for the port, then opens the browser.
  Clicking again while it is running just opens another tab — it never starts a second server.
- `launcher.py --status` prints `running` / `stopped`; `launcher.py --restart` stops then starts; `launcher.py --stop` (or `stop.bat`) shuts the
  server down, since nothing else in the UI exposes that.
- The server runs as **`pythonw.exe`, which cannot own a console**, and is detached from the launcher
  (its parent process exits immediately). So no terminal window exists that, when closed, would kill it —
  that was the failure mode of the old `run.bat`, which ran uvicorn *inside* a console window.
- **A stale instance keeps the port.** If an old server is running, a click only opens a browser tab; it
  will not replace it. Clear it once with `stop.bat` (or `launcher.py --restart`) if you had one open.
- Output is appended to `logs/server.log`. If startup fails a message box appears with the log tail —
  a silent failure under `pythonw` looks identical to "my click did nothing".
- `run.bat` now does the same thing as the icon. `make_icon.py` regenerates `app.ico` (7 sizes,
  PNG-compressed, no Pillow needed).
- `--uninstall-autostart` removes it. `logs/server.log` records `--- serve start ---` / `--- serve end ---`;
  a missing `serve end` after a crash means the process was killed, not shut down.

## Data

- `data/reader.db` — SQLite (works, files, cards, links, anchors, answers, refs)
- `data/captures/<paper>/` — figure snapshots
- PDFs are never moved or modified — the DB references your archive in place.

## Backup / restore

`backups/<timestamp>/` holds `reader.db`, `captures/` and a `manifest.json` with a
sha256 for every file.

```
# snapshot (also the ⤓ Backup button in the library)
.venv/Scripts/python backup.py

# list
.venv/Scripts/python backup.py --list

# restore over data/ (verifies every hash first; refuses without --force)
.venv/Scripts/python backup.py --restore backups/20261124_111948 --force
```

Restore is destructive and verifies the manifest first — a tampered or incomplete
snapshot is rejected rather than half-applied.

## Dev notes

- `uvicorn app:app --host 0.0.0.0 --port 8765`; tests: `.venv/Scripts/python -m pytest tests -q`
- Templates live in `templates/pass{1,2,3}.json` — edit prompts without touching code.
- If a UI change doesn't show: hard-refresh (Ctrl+Shift+R); static files are no-cache
  but the browser may hold an old copy from before that header existed.


## Licence

MIT — see `LICENSE`. Use it, fork it, no warranty.
