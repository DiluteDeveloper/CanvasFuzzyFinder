#!/usr/bin/env python3
"""
canvas_find.py - fuzzy-search a Canvas LMS course export (course-data.js).

Usage:
    python canvas_find.py -s "soil" my_gardening_course_example/
    python canvas_find.py -s "dirt" -b 2 -a 3 my_gardening_course_example/
    python canvas_find.py my_gardening_course_example/          # interactive mode

The last argument is the export's root folder; the data file is expected at
<root>/viewer/course-data.js. (A direct path to a .js file also works.)

The export is a single `window.COURSE_DATA = {...};` JSON blob. Each module
item's HTML is converted to plain text lines, and every line is fuzzy-matched
against your query. Results show the module and page the line came from,
plus optional context lines before/after.

Optional extras: -i DIR also searches every file in DIR.
Results from those files show the filename instead of a module name, and
-b / -a count words (N x 10) around the match instead of lines.

No third-party packages are required. If `rapidfuzz` is installed
(pip install rapidfuzz) it is used automatically and is much faster.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser

try:  # optional, faster and better fuzzy matching
    from rapidfuzz import fuzz as _rf_fuzz
except ImportError:  # pragma: no cover
    _rf_fuzz = None


# --------------------------------------------------------------------------
# Loading the export
# --------------------------------------------------------------------------

DATA_RELPATH = os.path.join("viewer", "course-data.js")

def resolve_data_file(root: str) -> str:
    """Turn the user's argument into the path of course-data.js.

    A folder is treated as the export root (<root>/viewer/course-data.js).
    A path to an existing file is used as-is.
    """
    root = os.path.expanduser(root)
    if os.path.isfile(root):
        return root
    if os.path.isdir(root):
        candidate = os.path.join(root, DATA_RELPATH)
        if os.path.isfile(candidate):
            return candidate
        raise ValueError(
            f"{candidate} not found. Is '{root}' the root folder of the course export?"
        )
    raise ValueError(f"'{root}' does not exist.")


def load_course(path: str):
    """Parse the JSON out of a `window.X = {...};` style JS file."""
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()

    candidates = sorted(i for i in (m.start() for m in re.finditer(r"[{\[]", text)))
    decoder = json.JSONDecoder()
    for start in candidates[:50]:
        try:
            data, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(data, (dict, list)) and data:
            return data
    raise ValueError(f"Could not find a JSON object in {path}")


# --------------------------------------------------------------------------
# HTML -> list of readable text lines
# --------------------------------------------------------------------------

_BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "ul", "ol", "table",
    "thead", "tbody", "tr", "blockquote", "pre", "figure", "figcaption", "dl",
    "dt", "dd", "hr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li",
}
_SKIP_TAGS = {"script", "style", "head", "template", "noscript"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._cell = 0
        self._pre = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "pre":
            self._pre += 1
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append("\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n\u2022 ")
        elif tag == "tr":
            self._cell = 0
            self.parts.append("\n")
        elif tag in ("td", "th"):
            if self._cell:
                self.parts.append(" | ")
            self._cell += 1
        elif tag == "iframe":
            a = dict(attrs)
            label = a.get("title") or a.get("src") or "embedded content"
            self.parts.append(f"\n[embedded: {label}]\n")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "pre":
            self._pre = max(0, self._pre - 1)
        if tag in _BLOCK_TAGS and tag != "br":
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            # Newlines in HTML source are just whitespace (except inside <pre>);
            # real line breaks come from block tags above.
            self.parts.append(data if self._pre else data.replace("\n", " "))


def html_to_lines(html: str) -> list[str]:
    parser = _TextExtractor()
    parser.feed(html or "")
    parser.close()
    lines = []
    for raw in "".join(parser.parts).split("\n"):
        line = re.sub(r"\s+", " ", raw.replace("\xa0", " ")).strip()
        # drop empties and lines that are only a bullet/heading/table marker
        if line.strip("#\u2022| ").strip():
            lines.append(line)
    return lines


# --------------------------------------------------------------------------
# Building the searchable index
# --------------------------------------------------------------------------

@dataclass
class Entry:
    module: str
    title: str
    kind: str
    lines: list[str]
    lower: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.lower = [l.lower() for l in self.lines]


def _make_entry(module: str, item: dict, kind: str) -> Entry:
    title = (item.get("title") or item.get("name") or "(untitled)").strip()
    body = html_to_lines(item.get("content") or "")

    # Pages usually repeat their title as the first heading; don't show it twice.
    if body and body[0].lstrip("# ").strip().lower() == title.lower():
        body = body[1:]

    lines = [f"# {title}"]
    if item.get("dueAt"):
        pts = item.get("pointsPossible")
        lines.append(f"Due: {item['dueAt']}" + (f"  |  Points: {pts}" if pts else ""))
    lines.extend(body)
    return Entry(module=module, title=title, kind=kind, lines=lines)


def build_entries(data) -> list[Entry]:
    entries: list[Entry] = []
    seen: set[str] = set()

    def add(module, item, kind):
        key = item.get("exportId")
        if key:
            if key in seen:
                return
            seen.add(key)
        entries.append(_make_entry(module, item, kind))

    if isinstance(data, dict) and "modules" in data:
        for mod in data.get("modules", []):
            for item in mod.get("items", []):
                add(mod.get("name", "(unnamed module)"), item, item.get("type", "Item"))
        # Things that aren't attached to a module (or are listed separately).
        for key, label in (
            ("pages", "Pages (not in a module)"),
            ("assignments", "Assignments"),
            ("discussion_topics", "Discussions"),
            ("quizzes", "Quizzes"),
        ):
            for item in data.get(key, []):
                add(label, item, item.get("type", key))
    else:
        # Unknown layout: walk everything and pick up anything with title + content.
        def walk(node, module="(unknown)"):
            if isinstance(node, dict):
                if "title" in node and "content" in node:
                    add(module, node, node.get("type", "Item"))
                for v in node.values():
                    walk(v, node.get("name", module) if "name" in node else module)
            elif isinstance(node, list):
                for v in node:
                    walk(v, module)
        walk(data)
    return entries


def load_extra_entries(extra_dir: str) -> list[Entry]:
    """One Entry per .txt file in extra_dir (not recursive).

    The filename takes the place of the module name; there is no page title.
    Blank lines are dropped, so line numbers count non-empty lines only.
    """
    extra_dir = os.path.expanduser(extra_dir)
    if not os.path.isdir(extra_dir):
        raise ValueError(f"'{extra_dir}' (from -i) is not a folder.")
    entries: list[Entry] = []
    for name in sorted(os.listdir(extra_dir), key=str.lower):
        path = os.path.join(extra_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
        except OSError as err:
            print(f"Warning: skipping {path}: {err}", file=sys.stderr)
            continue
        lines = []
        for raw in text.splitlines():
            line = re.sub(r"\s+", " ", raw.replace("\xa0", " ")).strip()
            if line:
                lines.append(line)
        if lines:
            entries.append(Entry(module=name, title="", kind="Extra", lines=lines))
    if not entries:
        print(f"Warning: no non-empty files found in {extra_dir}",
              file=sys.stderr)
    return entries


# --------------------------------------------------------------------------
# Fuzzy matching
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"[\w'\u2019-]+")


def _score_rapidfuzz(q: str, line: str, threshold: float):
    score = _rf_fuzz.partial_ratio(q, line, score_cutoff=threshold)
    if not score:
        return 0.0, None
    try:
        al = _rf_fuzz.partial_ratio_alignment(q, line)
        return float(score), (al.dest_start, al.dest_end)
    except Exception:
        return float(score), None


def _score_fallback(q: str, line: str, threshold: float):
    """Stdlib approximation of partial_ratio: best match of q against
    windows of 1-2 words either side of the query's word count."""
    idx = line.find(q)
    if idx != -1:
        return 100.0, (idx, idx + len(q))

    words = [(m.start(), m.end()) for m in _TOKEN.finditer(line)]
    if not words:
        return 0.0, None
    qn = max(1, len(_TOKEN.findall(q)))

    sm = SequenceMatcher(None, "", q, autojunk=False)  # seq2 (q) is cached
    best, span = 0.0, None
    for k in sorted({max(1, qn - 1), qn, qn + 1}):
        k = min(k, len(words))
        for i in range(len(words) - k + 1):
            s, e = words[i][0], words[i + k - 1][1]
            sm.set_seq1(line[s:e])
            if sm.real_quick_ratio() * 100 <= best or sm.quick_ratio() * 100 <= best:
                continue
            r = sm.ratio() * 100
            if r > best:
                best, span = r, (s, e)
    return (best, span) if best >= threshold else (0.0, None)


_score = _score_rapidfuzz if _rf_fuzz else _score_fallback


@dataclass
class Hit:
    entry: int
    line: int
    score: float
    span: tuple | None


def search(entries: list[Entry], query: str, threshold: float) -> list[Hit]:
    q = query.strip().lower()
    hits = []
    for ei, e in enumerate(entries):
        for li, low in enumerate(e.lower):
            score, span = _score(q, low, threshold)
            if score >= threshold:
                hits.append(Hit(ei, li, score, span))
    return hits


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

@dataclass
class Block:
    entry: int
    lo: int
    hi: int
    hits: dict  # line index -> Hit

    @property
    def best(self) -> float:
        return max(h.score for h in self.hits.values())


def build_blocks(entries, hits, before, after) -> list[Block]:
    by_entry = defaultdict(list)
    for h in hits:
        by_entry[h.entry].append(h)

    blocks: list[Block] = []
    for ei, hs in by_entry.items():
        hs.sort(key=lambda h: h.line)
        last = len(entries[ei].lines) - 1
        cur = None
        for h in hs:
            lo, hi = max(0, h.line - before), min(last, h.line + after)
            if cur and lo <= cur.hi + 1:  # overlapping/touching -> merge
                cur.hi = max(cur.hi, hi)
                cur.hits[h.line] = h
            else:
                cur = Block(ei, lo, hi, {h.line: h})
                blocks.append(cur)
    blocks.sort(key=lambda b: (-b.best, b.entry, b.lo))
    return blocks


# Files from -i get word-based context instead of line-based context:
# -b N / -a N mean N * WORDS_PER_UNIT words before/after the match.
WORDS_PER_UNIT = 10


@dataclass
class Word:
    line: int
    start: int
    end: int
    text: str


@dataclass
class WordBlock:
    entry: int
    lo: int          # index of first word shown
    hi: int          # index of last word shown
    words: list      # every Word in the entry (lo/hi index into this)
    hits: list       # Hit objects merged into this block
    match: set       # word indices that are part of a match

    @property
    def best(self) -> float:
        return max(h.score for h in self.hits)


def _entry_words(entry: Entry) -> tuple[list[Word], list[tuple[int, int]]]:
    """Flatten an entry into words (crossing line boundaries).

    Also returns, per line, the (first, last) word index on that line.
    """
    words: list[Word] = []
    line_range: list[tuple[int, int]] = []
    for li, line in enumerate(entry.lines):
        first = len(words)
        for m in re.finditer(r"\S+", line):
            words.append(Word(li, m.start(), m.end(), m.group()))
        line_range.append((first, len(words) - 1))
    return words, line_range


def _hit_word_range(entry, words, line_range, hit) -> tuple[int, int]:
    """Word indices covered by a hit; falls back to the whole line."""
    first, last = line_range[hit.line]
    line = entry.lines[hit.line]
    if hit.span and len(entry.lower[hit.line]) == len(line):
        s, e = hit.span
        idx = [i for i in range(first, last + 1)
               if words[i].start < e and words[i].end > s]
        if idx:
            return idx[0], idx[-1]
    return first, last


def build_word_blocks(entries, hits, before_words, after_words) -> list[WordBlock]:
    by_entry = defaultdict(list)
    for h in hits:
        by_entry[h.entry].append(h)

    blocks: list[WordBlock] = []
    for ei, hs in by_entry.items():
        words, line_range = _entry_words(entries[ei])
        if not words:
            continue
        spans = []
        for h in hs:
            ws, we = _hit_word_range(entries[ei], words, line_range, h)
            spans.append((ws, we, h))
        spans.sort(key=lambda t: (t[0], t[1]))

        last = len(words) - 1
        cur = None
        for ws, we, h in spans:
            lo, hi = max(0, ws - before_words), min(last, we + after_words)
            if cur and lo <= cur.hi + 1:  # overlapping/touching -> merge
                cur.hi = max(cur.hi, hi)
                cur.hits.append(h)
                cur.match.update(range(ws, we + 1))
            else:
                cur = WordBlock(ei, lo, hi, words, [h], set(range(ws, we + 1)))
                blocks.append(cur)
    return blocks


def _render_word_block(b: WordBlock, num_w: int, st, width) -> str:
    words, lo, hi = b.words, b.lo, b.hi
    pieces = []
    for i in range(lo, hi + 1):
        w = words[i].text
        if i in b.match:
            if i == lo or (i - 1) not in b.match:
                w = _HL_ON + w
            if i == hi or (i + 1) not in b.match:
                w = w + _HL_OFF
        pieces.append(w)
    text = " ".join(pieces)
    if lo > 0:
        text = "\u2026 " + text
    if hi < len(words) - 1:
        text += " \u2026"

    first_line = min(h.line for h in b.hits)
    prefix = f" > {first_line:>{num_w}}  "
    wrapped = textwrap.wrap(
        text, width=max(30, width - len(prefix)), break_long_words=True,
        break_on_hyphens=False,
    ) or [""]

    out, in_hl = [], False
    for i, chunk in enumerate(wrapped):
        pre = prefix if i == 0 else " " * len(prefix)
        if in_hl:  # a highlighted run was split across wrapped lines
            chunk = _HL_ON + chunk
        in_hl = chunk.rfind(_HL_ON) > chunk.rfind(_HL_OFF)
        if in_hl:
            chunk += _HL_OFF
        if st.on:
            chunk = (chunk.replace(_HL_ON, f"\x1b[{HL_STYLE}m")
                          .replace(_HL_OFF, "\x1b[0;2m"))
            chunk = st.dim(chunk)
        else:
            chunk = chunk.replace(_HL_ON, "\u00ab").replace(_HL_OFF, "\u00bb")
        out.append(pre + chunk)
    return "\n".join(out)


class Style:
    def __init__(self, enabled: bool):
        self.on = enabled

    def _w(self, code, s):
        return f"\x1b[{code}m{s}\x1b[0m" if self.on else s

    def head(self, s): return self._w("1;36", s)
    def match(self, s): return self._w("1", s)
    def dim(self, s): return self._w("2", s)
    def hl(self, s): return self._w(HL_STYLE, s)


_HL_ON, _HL_OFF = "\x00", "\x01"  # survive textwrap, swapped for ANSI later
# ANSI style for matched words. Reverse video + bold is readable on any
# terminal theme; for a coloured background try "1;30;43" (black on yellow)
# or "1;97;41" (white on red).
HL_STYLE = "1;7"


def _render_line(entry, li, num_w, is_match, hit, st, width):
    text = entry.lines[li]
    if is_match and st.on and hit and hit.span and len(entry.lower[li]) == len(text):
        s, e = hit.span
        text = text[:s] + _HL_ON + text[s:e] + _HL_OFF + text[e:]

    marker = ">" if is_match else " "
    prefix = f" {marker} {li:>{num_w}}  "
    wrapped = textwrap.wrap(
        text, width=max(30, width - len(prefix)), break_long_words=True,
        break_on_hyphens=False,
    ) or [""]

    out = []
    for i, chunk in enumerate(wrapped):
        pre = prefix if i == 0 else " " * len(prefix)
        if st.on:
            chunk = chunk.replace(_HL_ON, f"\x1b[{HL_STYLE}m").replace(_HL_OFF, "\x1b[0;1m")
        body = st.match(chunk) if is_match else st.dim(chunk)
        out.append(pre + body)
    return "\n".join(out)


def print_results(entries, hits, opts, st):
    if not hits:
        print("No matches. Try a lower threshold (-t / :t) or a shorter query.")
        return 0

    total = len(hits)
    hits = sorted(hits, key=lambda h: (-h.score, len(entries[h.entry].lines[h.line]),
                                       h.entry, h.line))[: opts.max_results]
    extra = {i for i, e in enumerate(entries) if e.kind == "Extra"}
    blocks = build_blocks(entries, [h for h in hits if h.entry not in extra],
                          opts.before, opts.after)
    blocks += build_word_blocks(entries, [h for h in hits if h.entry in extra],
                                opts.before * WORDS_PER_UNIT,
                                opts.after * WORDS_PER_UNIT)
    blocks.sort(key=lambda b: (-b.best, b.entry, b.lo))  # best first
    pages = len({b.entry for b in blocks})

    width = min(shutil.get_terminal_size((100, 24)).columns, 110) - 1
    shown = len(hits)
    print(st.head(f"{total} matching line{'s' if total != 1 else ''}"
                  + (f" (showing top {shown})" if shown < total else "")
                  + f" across {pages} page{'s' if pages != 1 else ''}"))
    print()

    # Print worst-to-best so the top result lands at the bottom, next to the
    # prompt. The [n] labels are still ranks: [1] is the best match.
    for n, b in reversed(list(enumerate(blocks, 1))):
        e = entries[b.entry]
        title = f"{e.module}  \u203a  {e.title}" if e.title else e.module
        print(st.head(f"[{n}] {title}") + st.dim(f"   (score {b.best:.0f})"))
        if isinstance(b, WordBlock):
            num_w = len(str(len(e.lines) - 1))
            print(_render_word_block(b, num_w, st, width))
            print()
            continue
        num_w = len(str(b.hi))
        if b.lo > 0:
            print(st.dim(" " * (num_w + 5) + "\u2026"))
        for li in range(b.lo, b.hi + 1):
            print(_render_line(e, li, num_w, li in b.hits, b.hits.get(li), st, width))
        if b.hi < len(e.lines) - 1:
            print(st.dim(" " * (num_w + 5) + "\u2026"))
        print()
    return shown


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

HELP = """\
Type text to fuzzy-search. Commands:
  :b N    context before each match: N lines (N x 10 words for -i files) (now {before})
  :a N    context after each match: N lines (N x 10 words for -i files)  (now {after})
  :n N    max matching lines to show           (now {max_results})
  :t N    match threshold 0-100, lower = fuzzier (now {threshold})
  :show   show current settings
  :help   show this help
  :q      quit  (or Ctrl-D / Ctrl-C)"""


def interactive(entries, opts, st):
    try:
        import readline  # noqa: F401  (enables arrow keys / history)
    except ImportError:
        pass
    extras = sum(e.kind == "Extra" for e in entries)
    loaded = f"{len(entries) - extras} pages"
    if extras:
        loaded += f" + {extras} extra file{'s' if extras != 1 else ''}"
    print(f"Loaded {loaded}. Type a search, or :help for commands.")
    while True:
        try:
            q = input("\nsearch> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not q:
            continue
        if q.startswith(":"):
            parts = q[1:].split()
            cmd, arg = (parts[0].lower() if parts else ""), parts[1:]
            if cmd in ("q", "quit", "exit"):
                return
            if cmd in ("help", "h", "?"):
                print(HELP.format(**vars(opts)))
            elif cmd == "show":
                print(f"before={opts.before} after={opts.after} "
                      f"max_results={opts.max_results} threshold={opts.threshold}")
            elif cmd in ("b", "a", "n", "t") and arg:
                try:
                    val = float(arg[0]) if cmd == "t" else int(arg[0])
                    if val < 0:
                        raise ValueError
                except ValueError:
                    print("Need a non-negative number.")
                    continue
                setattr(opts, {"b": "before", "a": "after",
                               "n": "max_results", "t": "threshold"}[cmd], val)
                print("OK")
            else:
                print("Unknown command. Try :help")
            continue
        print_results(entries, search(entries, q, opts.threshold), opts, st)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Fuzzy-search a Canvas LMS course export (course-data.js).",
        epilog="Omit -s to enter interactive mode. "
               "See README.md for full documentation.",
    )
    ap.add_argument("-s", "--string", help="text to fuzzy search for")
    ap.add_argument("-b", "--before", type=int, default=0, metavar="N",
                    help="lines of context to show before each match (default 0); "
                         "for files from -i this is N x 10 words instead")
    ap.add_argument("-a", "--after", type=int, default=0, metavar="N",
                    help="lines of context to show after each match (default 0); "
                         "for files from -i this is N x 10 words instead")
    ap.add_argument("-n", "--max-results", type=int, default=10, metavar="N",
                    help="max matching lines to show (default 10)")
    ap.add_argument("-t", "--threshold", type=float, default=80, metavar="0-100",
                    help="minimum match score, lower = fuzzier (default 80)")
    ap.add_argument("-i", "--include", metavar="DIR",
                    help="also search every .txt file in this folder "
                         "(results show the filename instead of a module name)")
    ap.add_argument("--no-color", action="store_true", help="disable ANSI colours")
    ap.add_argument("root", metavar="ROOT",
                    help="root folder of the course export "
                         "(contains viewer/course-data.js)")
    opts = ap.parse_args(argv)

    for name in ("before", "after", "max_results"):
        if getattr(opts, name) < 0:
            ap.error(f"--{name.replace('_', '-')} must be >= 0")

    # Make unicode (e.g. emoji in titles) safe on any console / pipe.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    use_color = sys.stdout.isatty() and not opts.no_color and "NO_COLOR" not in os.environ
    if use_color and os.name == "nt":
        os.system("")  # enables ANSI escape handling in Windows consoles
    st = Style(use_color)

    try:
        data_file = resolve_data_file(opts.root)
        entries = build_entries(load_course(data_file))
        if opts.include:
            entries += load_extra_entries(opts.include)
    except (OSError, ValueError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2
    if not entries:
        print("Error: no pages with content found in that file "
              "(and no included .txt files).", file=sys.stderr)
        return 2

    if opts.string:
        shown = print_results(entries, search(entries, opts.string, opts.threshold), opts, st)
        return 0 if shown else 1

    interactive(entries, opts, st)
    return 0


if __name__ == "__main__":
    try:
        code = main()
        sys.stdout.flush()
    except BrokenPipeError:
        # Reader closed the pipe early (e.g. `| head`, or quitting `less`).
        # Point stdout at devnull so the interpreter's exit flush stays quiet.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        code = 0
    sys.exit(code)
