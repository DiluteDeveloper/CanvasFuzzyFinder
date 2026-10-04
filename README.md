# CanvasFuzzyFinder

Disclaimer: **This tool is in no way affiliated with the University of
the Sunshine Coast.**

Fuzzy-search the text of an offline **Canvas LMS course export**.
This has only been tested on UniSC Canvas module pages.

Point it at the export's root folder, type something (typos are fine), and it
shows you every matching line along with the **module** and **page** it came
from, plus as much surrounding context as you ask for.

```
$ python canvas_find.py -s "consent and transparency" -b 1 -a 1 ~/courses/ICT101

1 matching line across 1 page

[1] Module 2 Data collection  ›  Module 2.2: Data collection considerations   (score 100)
       …
    9  Accuracy: To reduce bias and errors, make sure data is gathered accurately.
 > 10  Consent and Transparency: Let people know about information being collected and how 
       to utilize it.
   11  Security and privacy: Prevent unwanted access to sensitive data.
       …
```

## Contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Usage](#usage)
- [Options](#options)
- [Interactive mode](#interactive-mode)
- [Reading the output](#reading-the-output)
- [How it works](#how-it-works)
- [Examples](#examples)
- [Exit codes](#exit-codes)
- [Limitations and tips](#limitations-and-tips)
- [Troubleshooting](#troubleshooting)
- [AI Acknowledgement](#ai-acknowledgement)

## Requirements

- **Python 3.9 or newer** (tested on 3.12). Nothing else is required.
- **Optional:** `pip install rapidfuzz` makes searching much faster and is used
  automatically if present. Without it the tool uses a built-in matcher based
  on the standard library, which takes about a second and a half on a
  1 MB export.

Developed and tested on Linux. On Windows, colour needs a terminal with ANSI
support (Windows Terminal, PowerShell 7); otherwise add `--no-color`.

## Quick start

Firstly, you need to go to the UniSC canvas modules page of the course
that you wish to fuzzy search, and click on "Export Course Content" in
the top right corner. This will generate the files that this tool
needs to search through. Once it is downloaded, extract the zip
archive with your tool of choice and the resulting folder is passed to
the tool as the **root folder** of the export.

The tool expects the **root folder** of the export, which must contain
`viewer/course-data.js`:

```
ICT101/                   <- this is the folder you pass in
└── viewer/
    ├── course-data.js    <- the data file the tool reads
    └── files/ ...
```

Run one search and exit:

```bash
python canvas_find.py -s "sampling strategies" path/to/ICT101
```

Or start an interactive session and search as many times as you like:

```bash
python canvas_find.py path/to/ICT101
```

## Usage

```
canvas_find.py [-s STRING] [-b N] [-a N] [-n N] [-t SCORE] [--no-color] ROOT
canvas_find.py -h
```

`ROOT` must be the **last** argument.

| Argument | Description |
| --- | --- |
| `ROOT` | Root folder of the course export. The tool reads `ROOT/viewer/course-data.js`. A leading `~` is expanded. As a convenience, if `ROOT` is a path to an existing *file*, that file is used directly. |

If `-s` is given, one search is run and the program exits. If `-s` is omitted,
[interactive mode](#interactive-mode) starts.

## Options

| Option | Default | Description |
| --- | --- | --- |
| `-s`, `--string STRING` | *(interactive)* | Text to search for. Case-insensitive. |
| `-b`, `--before N` | `0` | Lines of context to show **before** each match. |
| `-a`, `--after N` | `0` | Lines of context to show **after** each match. |
| `-n`, `--max-results N` | `10` | Maximum number of matching lines to show. The highest-scoring ones are kept, and the total found is always reported. |
| `-t`, `--threshold SCORE` | `80` | Minimum score (0–100) for a line to count as a match. **Lower is fuzzier**: more results, looser matches. `100` means exact substring matches only. |
| `--no-color` | off | Disable ANSI colour. Colour is also off automatically when output is piped or redirected, or when the `NO_COLOR` environment variable is set. |
| `-h`, `--help` | | Show the usage summary. |

To search for text that starts with a dash, use the `=` form so it isn't read as
an option: `--string=-1`.

## Interactive mode

Run without `-s` and you get a `search>` prompt. The export is loaded once, so
repeated searches are quick. If your Python has the `readline` module, arrow
keys and command history work as in a shell.

Type text to search for it. A line that starts with a colon is a **command**:

| Command | Effect |
| --- | --- |
| `:b N` | Set lines of context before each match |
| `:a N` | Set lines of context after each match |
| `:n N` | Set the maximum number of matching lines shown |
| `:t SCORE` | Set the match threshold (0–100) |
| `:show` | Print the current settings |
| `:help`, `:h`, `:?` | List the commands |
| `:q`, `:quit`, `:exit` | Leave (Ctrl-D or Ctrl-C also work) |

Settings start from whatever you gave on the command line and last until you
quit. Because colon-lines are commands, a search can't itself begin with `:`
in interactive mode.

A typical session:

```
$ python canvas_find.py path/to/ICT101
Loaded 95 pages. Type a search, or :help for commands.

search> sampling
...results...
search> :a 2
OK
search> :t 70
OK
search> sampling
...more, looser results, with 2 lines of context after each...
search> :q
```

## Reading the output

A summary line comes first: how many lines matched, how many are being shown,
and across how many pages. Then come the results, in **blocks**.

```
[2] Module 2 Data collection  ›  Module 2.2: Data collection considerations   (score 98)
      …
 > 2  ## What are data collection considerations?
      …
```

- **`[2]`** is the block's **rank**. `[1]` is the best.
- **Order:** blocks are printed from **worst to best**, so the best result is
  the last thing on screen, right above your prompt. The rank numbers count
  down as you scroll toward it.
- **Header:** `Module name  ›  Page title`, then the block's best score.
- **`>`** in the left margin marks a matching line, shown in bold. On a colour
  terminal, the part of the line that matched is highlighted too, even when it
  matched despite a typo. Lines without `>` are context and are dimmed.
- **Numbers** next to each line are its position within the page, starting
  at 0. They are positions in the converted text, not in the original HTML.
- **`…`** above or below a block means the page has more lines in that
  direction.
- **Merging:** if the context windows of two matches on the same page overlap
  or touch, they are joined into one block.
- **Wrapping:** long lines wrap to your terminal width (up to 110 columns).
  Wrapping doesn't change line counts or `-b`/`-a`.

## How it works

### What is searched

The export stores everything in a single `window.COURSE_DATA = {...};` JSON
blob. Only that JSON is read; no JavaScript is executed. The tool searches, in
this order:

1. Every item in every **module**, labelled with its module's name.
2. **Pages** that aren't attached to a module, labelled
   `Pages (not in a module)`.
3. **Assignments**, **Discussions**, and **Quizzes**, which Canvas stores
   separately from modules.

Each page is searched once. Items are de-duplicated by their Canvas
`exportId`, so a page listed both in a module and in the page list isn't
reported twice.

**Page titles are searchable.** `-s "Module 2.2"` finds the page of that name.
Module names are *displayed* with every result but are not searched
themselves.

### What counts as a "line"

Each page's HTML is converted to plain text and split at block-level elements.
So a *line* is a paragraph, a heading, a list item, or one table row. A little
structure is kept so results stay readable:

| In the page | Shown as |
| --- | --- |
| Headings | `#`, `##`, ... prefix |
| List items | `•` prefix |
| Table cells | separated by ` \| ` (one row per line) |
| Embedded video / frames | `[embedded: title]` |
| Images | left out |
| Links | link text only |

Line 0 of every page is its title (`# Title`). Assignments have a second line
with the due date and points.

### Scoring

Each line gets a score from 0 to 100 for how well the best-matching *part* of
the line resembles your search text. An exact (case-insensitive) substring
scores 100. A line is a match when its score is at least the threshold
(`-t`, default 80).

Because it's the best-matching *part* that counts, a short query can match
inside a long paragraph, and typos or different word endings still match.
Examples at the default threshold: `data colection` matches "data collection",
and `transparancy` matches "Transparency".

When more lines match than `-n` allows, the highest scores are kept. Ties are
broken in favour of **shorter lines**, since a short line that matches is
usually more specific than a long paragraph containing the same words.

> **Note:** with `rapidfuzz` installed, scoring uses its algorithm; without it,
> a built-in approximation is used. Exact matches score 100 in both and near
> misses score similarly, but the numbers can differ slightly, so the same
> threshold may behave a little differently.

## Examples

Search for a phrase, tolerating typos:

```bash
python canvas_find.py -s "data colection considerations" ~/courses/ICT101
```

Show two lines before and three after each match:

```bash
python canvas_find.py -s "consent and transparency" -b 2 -a 3 ~/courses/ICT101
```

Find a page by its title:

```bash
python canvas_find.py -s "Module 2.2" -n 3 ~/courses/ICT101
```

Be more forgiving and show more results:

```bash
python canvas_find.py -s "dashbord" -t 70 -n 25 ~/courses/ICT101
```

Page through a long result set (best result last; press `G` in `less` to jump
to it):

```bash
python canvas_find.py -s "data" -n 100 --no-color ~/courses/ICT101 | less
```

Use it in a script (the exit status tells you whether anything matched):

```bash
if python canvas_find.py -s "privacy" --no-color ~/courses/ICT101 > /dev/null; then
    echo "mentioned somewhere"
fi
```

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | At least one match was found, or interactive mode ended normally |
| `1` | A search was given with `-s` and nothing matched |
| `2` | The data file couldn't be found or read, it had no usable pages, or the command line was invalid |

If you pipe output into something that exits early (`head`, quitting `less`),
the tool stops quietly with no error message.

## Limitations and tips

- **Only text inside `course-data.js` is searched.** The contents of attached
  files (slides, PDFs, spreadsheets) and of images are not included.
- **Matching is one line at a time.** A phrase split across two paragraphs, list
  items, or table rows won't match as a whole. Search a shorter piece of it, or
  use `-b`/`-a` to see the neighbouring lines.
- **Repeated results can be real.** If the course itself repeats a section (one
  page in the sample export contains the same block three times), you'll see
  it three times. That's faithful to the data, not a search fault.
- **Very short queries match a lot.** One or two characters will match almost
  anything at any threshold. Lengthen the query or raise `-t`.
- **Too few results?** Lower `-t` (try 70) or use fewer words. **Too many?**
  Raise `-t` (try 90) or use more specific words.

## Troubleshooting

| Message | Cause and fix |
| --- | --- |
| `.../viewer/course-data.js not found. Is '...' the root folder of the course export?` | `ROOT` exists but doesn't contain `viewer/course-data.js`. Check you're pointing at the folder *above* `viewer/`. |
| `The program 'python' is not in your PATH.` | Python may not been installed correctly. on some systems, the command may be `python3` instead. |
| `python is not recognized as an internal or external command, operable program or batch file.` | Python may not been installed correctly. on some systems, the command may be `python3` instead. |
| `'...' does not exist.` | The path is wrong or misspelled. |
| `Could not find a JSON object in ...` | The file isn't in the expected `window.COURSE_DATA = {...};` format, or is truncated or corrupt. |
| `no pages with content found in that file.` | The JSON loaded but has no pages with a `title` and `content`. |
| `the following arguments are required: ROOT` | You forgot the folder argument. It must come last. |
| Garbled characters (for example `â€º` instead of `›`) | The tool always writes UTF-8, so your terminal is set to a different encoding. Switch the terminal to UTF-8. |
| `No matches.` | Nothing scored at or above the threshold. Lower `-t` or shorten the query. |

## AI Acknowledgement

This tool and most of the preceding documentation was entirely created with Claude;
I have been in software engineering since 2018 and I have the experience 
to develop a tool like this, however, as this is a tool I am personally 
needing to use quickly, I made the choice to vibe-code it.
With that being said, please do not consider the quality of this tool
a reflection of my abilities; this is simply posted so that others may
use it; I have not checked over this code at all and am simply using
the tool that Claude generated with my prompts and guidance.
