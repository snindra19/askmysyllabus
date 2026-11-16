"""Conversion stage: PDF -> cleaned Markdown + removal log (DESIGN.md 11 and 11.3).

Runs on the laptop or a temporary job, never in the worker image.
    uv run --group convert python -m ingest.convert    # data/raw/*.pdf -> data/clean/
"""

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW, CLEAN = ROOT / "data" / "raw", ROOT / "data" / "clean"
EDGE = 3  # lines checked at the top and bottom of each page
MIN_ZIPF = 3.0  # wordfreq: bake-off correct joins scored >= 3.03, wrong merges <= 2.03

PAGE_NUMBER = re.compile(r"^(page\s*\|?\s*)?\d{1,3}(\s+of\s+\d{1,3})?$", re.I)
HTML = re.compile(r"<!--.*?-->|</?[A-Za-z][A-Za-z0-9]*(\s[^<>]*)?/?>", re.S)
LINE_END_DASH = re.compile(r"(\w+)([-‐–])$")
WORD = re.compile(r"\w+")
B, E = r"(?<![^\W_])", r"(?![^\W_])"  # word boundaries where "_" (Markdown emphasis) is not a word character


def strip_html(text):
    return HTML.sub(" ", text)


def is_table(line):
    return line.lstrip().startswith("|")


def plain(line):
    return " ".join(re.sub(r"[#*_`>]", " ", line).split()).lower()


def is_page_number(line):
    return not is_table(line) and bool(PAGE_NUMBER.match(plain(line)))


def remove_repeats(pages):
    """Drop standalone page numbers, and edge lines repeated on 2+ pages (keeping the first copy)."""
    page_lines = [p.splitlines() for p in pages]

    def edges(lines):
        idx = [i for i, l in enumerate(lines) if l.strip() and not is_table(l) and not is_page_number(l)]
        return set(idx[:EDGE] + idx[-EDGE:])

    seen = defaultdict(set)
    for n, lines in enumerate(page_lines):
        for i in edges(lines):
            seen[plain(lines[i])].add(n)  # exact text: ignoring digits merged "Module 3 ..." with "Module 5 ..."

    out, log, kept = [], [], set()
    for n, lines in enumerate(page_lines):
        edge, new = edges(lines), []
        for i, line in enumerate(lines):
            rule = None
            if is_page_number(line):
                rule = "page number"
            elif i in edge and len(seen[plain(line)]) >= 2:
                if plain(line) in kept:
                    rule = "margin repeat"
                kept.add(plain(line))
            if rule:
                log.append({"page": n + 1, "rule": rule, "text": line.strip()})
            else:
                new.append(line)
        out.append(re.sub(r"\n{3,}", "\n\n", "\n".join(new)))
    return out, log


def dash_pairs(page_texts):
    """(page, left, dash, right) for every line that ends in a dash, from PyMuPDF raw text."""
    lines = [(n, l.strip()) for n, t in enumerate(page_texts, start=1) for l in t.splitlines() if l.strip()]
    pairs = []
    for (page, a), (_, b) in zip(lines, lines[1:]):
        left, right = LINE_END_DASH.search(a), WORD.match(b)
        if left and right:
            pairs.append((page, left.group(1), left.group(2), right.group(0)))
    return pairs


def repair_dashes(text, pairs, vocabulary):
    """Undo the converter's line-end dash removal where the joined form is not a real word."""
    from wordfreq import zipf_frequency

    log, done = [], set()
    for page, left, dash, right in pairs:
        if (left, dash, right) in done:
            continue
        done.add((left, dash, right))
        joined, hyphenated = left + right, left + dash + right
        pattern = rf"{B}{re.escape(joined)}{E}"
        count = len(re.findall(pattern, text))
        if not count:
            kept = re.search(rf"{B}{re.escape(left)}\s*[-‐–]\s*{re.escape(right)}{E}", text)
            decision = "converter kept dash" if kept else "not found"
        elif left[-1].isdigit() and right[0].isdigit():
            decision = "restored dash (digits)"
        elif joined.lower() in vocabulary:
            decision = "joined (in corpus)"
        elif zipf_frequency(joined, "en") >= MIN_ZIPF:
            decision = "joined (common word)"
        else:
            decision = "restored hyphen"
        if decision.startswith("restored"):
            text = re.sub(pattern, hyphenated, text)
        log.append({"page": page, "rule": "dash", "pair": hyphenated, "decision": decision, "count": count})
    return text, log


def join_pages(pages):
    """Start each page with <!-- page N --> so later steps can find page 1 and cite pages."""
    return "".join(f"<!-- page {n} -->\n{p.strip()}\n\n" for n, p in enumerate(pages, start=1))


def split_pages(markdown):
    return re.split(r"<!-- page \d+ -->\n", markdown)[1:]


def convert(pdf, vocabulary):
    """PDF -> (cleaned Markdown, removal log)."""
    import pymupdf
    import pymupdf4llm

    chunks = pymupdf4llm.to_markdown(str(pdf), page_chunks=True, show_progress=False)
    pages, log = remove_repeats([strip_html(c["text"]) for c in chunks])
    with pymupdf.open(pdf) as doc:
        pairs = dash_pairs([page.get_text() for page in doc])
    markdown, dash_log = repair_dashes(join_pages(pages), pairs, vocabulary)
    return markdown, log + dash_log


def vocabulary(texts):
    """Lowercase words in raw PDF text, so dash repair can ask whether a joined form occurs anywhere.
    URLs and mixed-case tokens are skipped: they hold glued words ("academicintegrity", "UniversitySanctioned")."""
    words = set()
    for text in texts:
        for w in WORD.findall(re.sub(r"\S*(://|www\.)\S*", " ", text)):
            if w.islower() or w.isupper() or w.istitle():
                words.add(w.lower())
    return words


if __name__ == "__main__":
    import pymupdf

    pdfs = sorted(RAW.glob("*.pdf"))
    texts = []
    for pdf in pdfs:
        with pymupdf.open(pdf) as doc:
            texts.append("".join(page.get_text() for page in doc))
    words = vocabulary(texts)
    CLEAN.mkdir(parents=True, exist_ok=True)
    for pdf in pdfs:
        markdown, log = convert(pdf, words)
        (CLEAN / f"{pdf.stem}.md").write_text(markdown)
        (CLEAN / f"{pdf.stem}.log.json").write_text(json.dumps(log, indent=1, ensure_ascii=False))
        print(f"{pdf.name}: {len(log)} log entries", flush=True)
