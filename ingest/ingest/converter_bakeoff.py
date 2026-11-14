"""PDF converter bake-off (DESIGN.md Section 13).

Run one converter per process so model memory is released between them:
    uv run --group convert python -m ingest.converter_bakeoff docling
    uv run --group convert python -m ingest.converter_bakeoff marker
    uv run --group convert python -m ingest.converter_bakeoff pymupdf4llm
    uv run --group convert python -m ingest.converter_bakeoff report
"""

import json
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "bakeoff"
REPORT = ROOT / "eval" / "reports" / "converter_bakeoff.md"
CONVERTERS = ["docling", "marker", "pymupdf4llm"]

TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
HEADING = re.compile(r"^#{1,6}\s+\S")


def load_converter(name):
    """Return a function PDF path -> Markdown, with models already loaded."""
    if name == "docling":
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions(accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU))
        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        converter.initialize_pipeline(InputFormat.PDF)
        return lambda path: converter.convert(path).document.export_to_markdown()

    if name == "marker":
        from marker.config.parser import ConfigParser
        from marker.models import create_model_dict
        from marker.output import text_from_rendered

        # Same setup as marker's CLI on a CPU machine (mode "fast", no inference server).
        parser = ConfigParser({"mode": "fast", "output_format": "markdown", "disable_tqdm": True})
        converter = parser.get_converter_cls()(
            config=parser.generate_config_dict(),
            artifact_dict=create_model_dict(load_indra=True, no_server=True),
            processor_list=parser.get_processors(),
            renderer=parser.get_renderer(),
        )
        return lambda path: text_from_rendered(converter(str(path)))[0]

    if name == "pymupdf4llm":
        import pymupdf4llm

        return lambda path: pymupdf4llm.to_markdown(str(path))

    raise SystemExit(f"unknown converter {name!r}; choose from {CONVERTERS + ['report']}")


def coverage(markdown, baseline_words):
    """Share of the plain-text extraction's characters (as whole words) that appear in the Markdown."""
    found = Counter(re.findall(r"\w+", markdown.lower()))
    total = sum(len(w) * n for w, n in baseline_words.items())
    kept = sum(len(w) * min(n, found[w]) for w, n in baseline_words.items())
    return kept / total if total else None


def run(name):
    out_dir = OUT / name
    out_dir.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    convert = load_converter(name)
    results = {"load_seconds": round(time.perf_counter() - start, 1), "files": {}}

    for pdf in sorted(RAW.glob("*.pdf")):
        with pymupdf.open(pdf) as doc:
            texts = [page.get_text() for page in doc]
        baseline_words = Counter(re.findall(r"\w+", "".join(texts).lower()))
        row = {"pages": len(texts), "textless_pages": sum(len(t.strip()) < 50 for t in texts)}
        start = time.perf_counter()
        try:
            markdown = convert(pdf)
        except Exception as e:
            row.update(seconds=round(time.perf_counter() - start, 1), error=f"{type(e).__name__}: {str(e)[:120]}")
        else:
            row["seconds"] = round(time.perf_counter() - start, 1)
            (out_dir / f"{pdf.stem}.md").write_text(markdown)
            lines = markdown.splitlines()
            row.update(
                coverage=coverage(markdown, baseline_words),
                tables=sum(bool(TABLE_SEPARATOR.match(line)) for line in lines),
                headings=sum(bool(HEADING.match(line)) for line in lines),
            )
        results["files"][pdf.name] = row
        print(f"{name} {pdf.name}: {row}", flush=True)

    (out_dir / "metrics.json").write_text(json.dumps(results, indent=2))


def fmt(value, spec=""):
    return "–" if value is None else format(value, spec)


def disagreement(rows):
    """Spread across successful converters; missing text weighs most. Errors are listed separately."""
    ok = [r for r in rows if "error" not in r]
    if len(ok) < 2:
        return 0.0
    score = 5 * (max(r["coverage"] for r in ok) - min(r["coverage"] for r in ok))
    for key in ("tables", "headings"):
        values = [r[key] for r in ok]
        score += (max(values) - min(values)) / (max(values) + 1)
    return score


def report():
    metrics = {}
    for name in CONVERTERS:
        path = OUT / name / "metrics.json"
        if path.exists():
            metrics[name] = json.loads(path.read_text())
    names = list(metrics)
    files = sorted({f for m in metrics.values() for f in m["files"]})
    short = "/".join(n[0].upper() for n in names)

    lines = [
        "# PDF converter bake-off",
        "",
        "DESIGN.md Section 13. Generated by `ingest/ingest/converter_bakeoff.py`; metrics only, no syllabus text.",
        "Outputs: `data/bakeoff/<converter>/<file>.md`. CPU only.",
        "",
        "- **Coverage**: share of the PyMuPDF plain-text extraction's characters, counted as whole words, that appear in the Markdown.",
        "  pymupdf4llm uses the same text engine as the baseline, so its coverage is biased upward;",
        "  coverage does not measure reading order or tables, and above 100% is not possible.",
        "  Pages with no text layer (scans, images) are missing from the baseline, so OCR text there earns no credit.",
        "- **Tables**: Markdown pipe tables (separator rows). **Headings**: `#` lines.",
        "- **Time**: per-file conversion time; model loading is excluded and listed separately.",
        "",
        "## Overall",
        "",
        "| Converter | Files OK | Errors | Model load (s) | Total time (s) | Median s/file | s/page | Mean coverage | Min coverage | Tables | Headings | Files with 0 headings |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in names:
        rows = list(metrics[name]["files"].values())
        ok = [r for r in rows if "error" not in r]
        seconds = [r["seconds"] for r in ok]
        lines.append(
            f"| {name} | {len(ok)}/{len(rows)} | {len(rows) - len(ok)} | {metrics[name]['load_seconds']} "
            f"| {sum(seconds):.1f} | {fmt(statistics.median(seconds) if seconds else None, '.1f')} "
            f"| {fmt(sum(seconds) / sum(r['pages'] for r in ok) if ok else None, '.2f')} "
            f"| {fmt(statistics.mean(r['coverage'] for r in ok) if ok else None, '.1%')} "
            f"| {fmt(min(r['coverage'] for r in ok) if ok else None, '.1%')} "
            f"| {sum(r['tables'] for r in ok)} | {sum(r['headings'] for r in ok)} "
            f"| {sum(r['headings'] == 0 for r in ok)} |"
        )

    lines += [
        "",
        f"## Per file ({short} = {', '.join(names)})",
        "",
        f"| File | Pages (no text) | Time s ({short}) | Coverage ({short}) | Tables ({short}) | Headings ({short}) | Disagreement |",
        "|---|---|---|---|---|---|---|",
    ]
    scores = {}
    for f in files:
        rows = [metrics[n]["files"].get(f, {"error": "missing"}) for n in names]
        scores[f] = disagreement(rows)
        cell = lambda key, spec="": " / ".join("err" if "error" in r else fmt(r.get(key), spec) for r in rows)
        r0 = next((r for r in rows if "pages" in r), {})
        lines.append(
            f"| {f} | {fmt(r0.get('pages'))} ({fmt(r0.get('textless_pages'))}) | {cell('seconds', '.1f')} | {cell('coverage', '.0%')} "
            f"| {cell('tables')} | {cell('headings')} | {scores[f]:.2f} |"
        )

    errors = [(n, f, r["error"]) for n in names for f, r in metrics[n]["files"].items() if "error" in r]
    lines += ["", "## Errors", ""]
    lines += [f"- {n} / {f}: `{e}`" for n, f, e in errors] or ["None."]

    lines += [
        "",
        "## Biggest disagreements (compare side by side)",
        "",
        "Disagreement = 5 × coverage range + table-count range / (max + 1) + heading-count range / (max + 1),",
        "over converters that succeeded. Missing text weighs most; `+ 1` keeps 0-vs-1 table counts from dominating.",
        "",
    ]
    for f in sorted(files, key=scores.get, reverse=True)[:4]:
        stem = Path(f).stem
        paths = ", ".join(f"`data/bakeoff/{n}/{stem}.md`" for n in names if (OUT / n / f"{stem}.md").exists())
        lines.append(f"1. **{f}** (score {scores[f]:.2f}): {paths}")

    # Keep the hand-written Decision section across regenerations.
    old = REPORT.read_text() if REPORT.exists() else ""
    decision = old[old.find("\n## Decision"):] if "\n## Decision" in old else ""
    REPORT.write_text("\n".join(lines) + "\n" + decision)
    print(f"wrote {REPORT}")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        report() if arg == "report" else run(arg)
