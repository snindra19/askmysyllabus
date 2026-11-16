"""Manifest builder: data/sources.csv + data/clean/*.md (+ data/manifest_overrides.csv) -> data/manifest.csv.

    uv run --group convert python -m ingest.manifest

Values come only from the source URL and the cleaned Markdown (page 1; professor may come from a later page);
anything not found stays blank, with confidence "low" and a note. The sources.csv course column is only a hint to
cross-check; its optional file_name column links a URL to a PDF directly (e.g. Google Docs). Hand fixes go in
manifest_overrides.csv, which is applied last and never written.
"""

import csv
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from ingest.convert import CLEAN, RAW, split_pages

ROOT = Path(__file__).resolve().parents[2]
SOURCES, MANIFEST = ROOT / "data" / "sources.csv", ROOT / "data" / "manifest.csv"
OVERRIDES = ROOT / "data" / "manifest_overrides.csv"
FIELDS = ["file_name", "source_url", "university", "term", "class_number", "course_code", "title", "professor",
          "confidence", "notes"]
OVERRIDABLE = FIELDS[1:-2]  # everything except file_name (the key) and the derived confidence / notes

# Known source hosts. The syllabus bucket is not on a university domain, so it is mapped by hand.
SOURCE_HOSTS = {"class-syllabus-prod.s3.dualstack.us-west-2.amazonaws.com": "ASU"}
ASU_SEASONS = {"1": "Spring", "4": "Summer", "7": "Fall"}

CODE = re.compile(r"\b((?:[A-Z]{3}/)*[A-Z]{3}) (\d{3}(?:/\d{3})*)\b")
TERM = re.compile(r"\b(Spring|Summer|Fall)\s+(?:([A-C])\s+)?(20\d\d)\b|\b(20\d\d)\s+(Spring|Summer|Fall)(?:\s+([A-C])\b)?", re.I)
HONORIFIC = re.compile(r"^((Prof|Dr|Mr|Ms|Mrs)\.?|Professor)\s+", re.I)
LABELS = r"(Instructors?|Professors?|Faculty|Contact)"
INLINE_LABEL, BARE_LABEL = re.compile(rf"\b{LABELS}\s*:", re.I), re.compile(rf"^{LABELS}(\s+Information)?\s*:?$", re.I)
NAME_STOP = {"Office", "Email", "E-Mail", "Phone", "TA", "TAs", "Time", "Times", "Meeting", "Hours", "Location", "TBA", "TBD"}


def parse_url(url):
    """File name (query ignored), university by host, and ASU term code / class number from the path."""
    parts = urlsplit(url)
    m = re.search(r"/(\d{4})/(\d+)/[^/]+\.pdf$", parts.path)
    return {"file_name": parts.path.rsplit("/", 1)[-1], "university": SOURCE_HOSTS.get(parts.netloc, ""),
            "term_code": m.group(1) if m else "", "class_number": m.group(2) if m else ""}


def asu_term(code):
    """ASU term code: 2 + two-digit year + season digit (1 Spring, 4 Summer, 7 Fall), e.g. 2267 = Fall 2026."""
    season = ASU_SEASONS.get(code[3:]) if len(code) == 4 else None
    return f"{season} 20{code[1:3]}" if season else ""


def clean_line(line):
    """Markdown line -> plain text. Joins bold drop caps ("**D** atabase" -> "Database")."""
    line = re.sub(r"(?<=\b[A-Z])\*\*\s+(?=[a-z])", "", line)
    return " ".join(re.sub(r"[*_`#|]", " ", line).split())


def find_term(text):
    m = TERM.search(clean_line(text))
    if not m:
        return "", ""
    season, session, year = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(5), m.group(6), m.group(4))
    return f"{season.capitalize()} {year}", (session or "").upper()


def find_course(page):
    """(course code, title) from the first line on page 1 that holds a course code."""
    lines = [clean_line(l) for l in page.splitlines() if l.strip()]
    for line in lines:
        m = CODE.search(line)
        if not m:
            continue
        title = next((re.split(r"Course (?:Title|Name):", l, maxsplit=1)[1] for l in lines
                      if re.search(r"Course (?:Title|Name):", l)), line[m.end():])
        title = CODE.sub("", title)                             # cross-listed or repeated codes
        title = re.sub(r"^[\s:,–—-]+", "", title)
        paren = re.match(r"\(([^)]*)\)", title)                 # "CSE 522 (Real-Time Embedded Systems) Fall 2026"
        title = paren.group(1) if paren else title
        title = TERM.sub("", title)
        title = re.sub(r"\(\s*\)|\bSyllabus\b", "", title)
        return f"{m.group(1)} {m.group(2)}", re.sub(r"^[\s:,–—-]+|[\s:,–—-]+$", "", " ".join(title.split()))
    return "", ""


def find_professor(page):
    """Name after an "Instructor:" / "Professor:" / "Faculty:" / "Contact:" label, or on the line after a line
    that is only such a label (a heading), without titles."""
    lines = [clean_line(l) for l in page.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        m = BARE_LABEL.match(line) or INLINE_LABEL.search(line)
        if not m:
            continue
        value = line[m.end():].strip() or (lines[i + 1] if i + 1 < len(lines) else "")
        value = HONORIFIC.sub("", re.sub(r"^Name:\s*", "", value).strip())
        name = []
        for token in value.split():
            if token.endswith(":") or token in NAME_STOP or not re.match(r"^[A-Z][A-Za-z'’.-]*,?$", token):
                break
            name.append(token.rstrip(","))
            if token.endswith(","):
                break
        if len(name) >= 2:
            return " ".join(name)
    return ""


def hint_matches(hint, code, title):
    m = CODE.search(hint)
    if m:
        return bool(code) and m.group(2) == code.split(" ")[-1]
    simple = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    return bool(title) and (simple(hint) in simple(title) or simple(title) in simple(hint))


def source_file(source):
    """PDF a sources.csv row points to: its file_name column if set, else the URL's file name."""
    return (source.get("file_name") or "").strip() or parse_url(source["url"])["file_name"]


def build_rows(sources, docs):
    """sources: [{url, course, file_name?}], docs: {pdf file name: [page Markdown, ...]} -> (rows, problems)."""
    by_file = defaultdict(list)
    for s in sources:
        by_file[source_file(s)].append(s)
    urls = [s["url"] for s in sources]
    problems = {
        "pdfs_without_url": sorted(f for f in docs if f not in by_file),
        "urls_without_pdf": list(dict.fromkeys(s["url"] for s in sources if source_file(s) not in docs)),
        "duplicate_urls": list(dict.fromkeys(u for u in urls if urls.count(u) > 1)),
    }

    rows = []
    for file_name in sorted(docs):
        pages, matched = docs[file_name] or [""], by_file.get(file_name, [])
        page = pages[0]
        info = parse_url(matched[0]["url"]) if matched else {"university": "", "term_code": "", "class_number": ""}
        code, title = find_course(page)
        professor, professor_page = next(((p, n) for n, p in enumerate(map(find_professor, pages), 1) if p), ("", 0))
        term, session = find_term(page)
        notes, low, medium = [], False, False

        if not matched:
            low = True
            notes.append("no source URL")
            notes += [f"sources.csv row with course '{s['course']}' points to {s['url']}" for s in sources
                      if code and hint_matches(s["course"], code, title)]
        else:
            if len(matched) > 1:
                notes.append(f"{len(matched)} source rows share this URL")
            if not any(hint_matches(s["course"], code, title) for s in matched):
                low = True
                notes.append(f"course hint '{matched[0]['course']}' does not match page 1")
            if not info["university"]:
                low = True
                notes.append("unknown source host")

        url_term = asu_term(info["term_code"]) if info["university"] == "ASU" else ""
        if term and url_term and term != url_term:
            low = True
            notes.append(f"term code {info['term_code']} = {url_term} but page 1 says {term}")
        elif not term and url_term:
            term, medium = url_term, True
            notes.append("term from URL code (not on page 1)")
        elif not term:
            low = True
            notes.append("term not found on page 1")
        if session:
            notes.append(f"session {session}")

        for field, value in (("course code", code), ("title", title)):
            if not value:
                low = True
                notes.append(f"{field} not found on page 1")
        if not professor:
            low = True
            notes.append("professor not found")
        elif professor_page > 1:
            medium = True
            notes.append(f"professor from page {professor_page}")

        rows.append({"file_name": file_name, "source_url": matched[0]["url"] if matched else "",
                     "university": info["university"], "term": term, "class_number": info["class_number"],
                     "course_code": code, "title": title, "professor": professor,
                     "confidence": "low" if low else "medium" if medium else "high", "notes": "; ".join(notes)})
    return rows, problems


def apply_overrides(rows, overrides):
    """Hand fixes, applied last. Blank cells change nothing. Returns override file names with no manifest row."""
    by_file = {r["file_name"]: r for r in rows}
    unmatched = []
    for o in overrides:
        bad = [c for c in o if c != "file_name" and c not in OVERRIDABLE]
        if bad:
            raise ValueError(f"manifest_overrides.csv: columns {bad} cannot be overridden (allowed: {OVERRIDABLE})")
        row = by_file.get(o["file_name"])
        if not row:
            unmatched.append(o["file_name"])
            continue
        changed = [c for c in OVERRIDABLE if (o.get(c) or "").strip()]
        if changed:
            row.update({c: o[c].strip() for c in changed})
            row["confidence"] = "high"
            row["notes"] = "; ".join(filter(None, [row["notes"], f"overridden: {', '.join(changed)}"]))
    return unmatched


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


if __name__ == "__main__":
    docs = {}
    for pdf in sorted(RAW.glob("*.pdf")):
        md = CLEAN / f"{pdf.stem}.md"
        docs[pdf.name] = split_pages(md.read_text()) if md.exists() else []
    rows, problems = build_rows(read_csv(SOURCES), docs)
    problems["overrides_without_pdf"] = apply_overrides(rows, read_csv(OVERRIDES)) if OVERRIDES.exists() else []
    with open(MANIFEST, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {MANIFEST} ({len(rows)} rows)")
    for key, items in problems.items():
        print(f"{key}: {len(items)}")
        for item in items:
            print(f"  {item}")
