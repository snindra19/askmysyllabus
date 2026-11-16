"""Manifest builder. Patterns are real ones from the first pages of the current syllabi;
person names and emails are replaced with placeholders."""

import pytest

from ingest.manifest import apply_overrides, asu_term, build_rows, find_course, find_professor, find_term, parse_url

S3 = "https://class-syllabus-prod.s3.dualstack.us-west-2.amazonaws.com"


# --- source URLs ---

def test_parse_s3_url_ignores_query():
    assert parse_url(f"{S3}/2267/85438/syllabus-2267-85438.pdf?v=1776278978052") == {
        "file_name": "syllabus-2267-85438.pdf", "university": "ASU", "term_code": "2267", "class_number": "85438"}


def test_unknown_host_has_no_university():
    url = "https://docs.google.com/document/d/abc/edit?tab=t.0#heading"
    assert parse_url(url) == {"file_name": "edit", "university": "", "term_code": "", "class_number": ""}


@pytest.mark.parametrize("code, term", [("2267", "Fall 2026"), ("2261", "Spring 2026"), ("2264", "Summer 2026"),
                                        ("2251", "Spring 2025"), ("2269", "")])
def test_asu_term_codes(code, term):
    assert asu_term(code) == term


# --- term printed in the syllabus ---

@pytest.mark.parametrize("text, term, session", [
    ("# ASU... **Course Syllabus – Fall C 2026 CSE 535 Mobile Computing**", "Fall 2026", "C"),
    ("**(2026 Fall C – Number: 82398)**", "Fall 2026", "C"),
    ("# **Course Syllabus - Fall B 2026** CSE 540: Engineering Blockchain Applications", "Fall 2026", "B"),
    ("### **Syllabus for Fall 2026 Hybrid**", "Fall 2026", ""),
    ("**Term:** Fall 2026 **Subject:** Computer Science & Engineering (CSE) **Number:** 512", "Fall 2026", ""),
    ("## **(Fall 2026) (67489)**", "Fall 2026", ""),
    ("- Semester & Year: 8/20/2026 - 12/4/2026 (C)", "", ""),
])
def test_find_term(text, term, session):
    assert find_term(text) == (term, session)


# --- course code and title ---

@pytest.mark.parametrize("page, code, title", [
    ("# CSE 575: Statistical Machine Learning\nsubject to change", "CSE 575", "Statistical Machine Learning"),
    ("**Disclaimer** text\n# **CSE 543 Information Assurance and Security**\n## **(Fall 2026) (67489)**",
     "CSE 543", "Information Assurance and Security"),
    ("# ** CSE 572: Data Mining **", "CSE 572", "Data Mining"),
    ("# Welcome to CSE 546: Cloud Computing", "CSE 546", "Cloud Computing"),
    ("# CSE 494/559: Algorithms in Computational Biology\nFall 2026", "CSE 494/559", "Algorithms in Computational Biology"),
    ("# Syllabus of CSE 522 (Real-Time Embedded Systems) Fall 2026", "CSE 522", "Real-Time Embedded Systems"),
    ("# **Syllabus**\n## **Course Info**\n- Course Name: CSE 536, Advanced Operating Systems", "CSE 536",
     "Advanced Operating Systems"),
    ("# CSE/BMI/BME/CEN/RAS 507 – CSE 494 Image Processing and Analysis", "CSE/BMI/BME/CEN/RAS 507",
     "Image Processing and Analysis"),
    ("# **IEE/CSE/DSE 506 Computing for Data-Driven Optimization Fall 2026**", "IEE/CSE/DSE 506",
     "Computing for Data-Driven Optimization"),
    ("ASUEngineeringIra A. Fulton Schools\n# **Course Syllabus - Fall B 2026** CSE 540: Engineering Blockchain Applications",
     "CSE 540", "Engineering Blockchain Applications"),
    ("# ** SYLLABUS: CSE 573: Semantic Web Mining **", "CSE 573", "Semantic Web Mining"),
    ("**Term:** Fall 2026 **Subject:** Computer Science & Engineering (CSE) **Number:** 512\n"
     "**Course Title: D** istributed **D** atabase **S**  ystems (CSE 512)", "CSE 512", "Distributed Database Systems"),
    ("# ASU **Course Syllabus – Fall C 2026 CSE 535 Mobile Computing**\n- Number: 63981\n- Course Title: Mobile Computing",
     "CSE 535", "Mobile Computing"),
    ("# CSE 551: FOUNDATIONS OF ALGORITHMS", "CSE 551", "FOUNDATIONS OF ALGORITHMS"),
    ("# CSE 510-Fall 2026 Syllabus\n**Instructor: Dr. Jane Doe** Email: x", "CSE 510", ""),
    ("Classroom: HLMK 351 (Tempe)\nOffice: BYENG 488", "", ""),
])
def test_find_course(page, code, title):
    assert find_course(page) == (code, title)


# --- professor ---

@pytest.mark.parametrize("page, name", [
    ("- **Instructor:** Prof.  Jane Doe, jdoe@example.edu\n- **Instructor:** Office: BYENG M1-38", "Jane Doe"),
    ("**Instructor:** Jane Doe **Meeting Times:** M&W 10:30 am", "Jane Doe"),
    ("Instructor: Dr. Jane Doe Office: BYENG 488 Email: jdoe@example.edu", "Jane Doe"),
    ("|**Instructor:**|Jane Doe|**Time:**|MW 3:00PM–4:15PM|", "Jane Doe"),
    ("### **Instructor: Professor Jane Q. Doe**\nEmail: jdoe@example.edu", "Jane Q. Doe"),
    ("Instructor\nProf. Jane Doe Office Hours at BYENG 592: Mondays 2:00 pm", "Jane Doe"),
    ("# **Instructor**\n**Name:**  Jane Doe, Ph.D.\nAssistant Teaching Professor", "Jane Doe"),
    ("Instructor: Jane Doe TA: John Roe E-Mail:  jdoe@example.edu", "Jane Doe"),
    ("Instructor: Jane Doe   email: jdoe@example.edu    Office hours: TBA", "Jane Doe"),
    ("**Instructor: Dr. Jane Doe** Email: jdoe@example.edu", "Jane Doe"),
    ("- Instructor: Jane Doe\n- Email: jdoe@example.edu", "Jane Doe"),
    ("Instructor: TBA", ""),
    ("- **Instructor’s office hours:** Monday & Wednesday 4 pm - 5 pm.", ""),
    ("**Disclaimer:** The instructor may update the syllabus.\nTA: John Roe", ""),
])
def test_find_professor(page, name):
    assert find_professor(page) == name


# --- rows: matching, cross-checks, confidence ---

PAGE_575 = "# CSE 575: Statistical Machine Learning\nFall 2026\n**Instructor:** Jane Doe **Meeting Times:** M&W"
PAGE_573 = "# ** SYLLABUS: CSE 573: Semantic Web Mining **\n**(2026 Fall C – Number: 82398)**\nInstructor: John Roe TA: X"
PAGE_545 = "# **Course Syllabus** CSE 545: Software Security\n## **NOTICE**"


def test_rows_matching_cross_checks_and_confidence():
    sources = [
        {"url": f"{S3}/2267/65678/syllabus-2267-65678.pdf?v=1", "course": "CSE 575: Statistical Machine Learning"},
        {"url": f"{S3}/2267/65678/syllabus-2267-65678.pdf?v=1", "course": "CSE 573: Semantic Web Mining"},
        {"url": "https://docs.google.com/document/d/abc/edit?tab=t.0", "course": "CSE 545: Software Security"},
        {"url": f"{S3}/2261/11111/syllabus-2261-11111.pdf?v=2", "course": "CSE 999 Nothing"},
    ]
    docs = {"syllabus-2267-65678.pdf": [PAGE_575], "syllabus-2267-82398.pdf": [PAGE_573], "CSE545-Syllabus.pdf": [PAGE_545]}
    rows, problems = build_rows(sources, docs)
    by_file = {r["file_name"]: r for r in rows}

    ok = by_file["syllabus-2267-65678.pdf"]
    assert ok == {**ok, "source_url": sources[0]["url"], "university": "ASU", "term": "Fall 2026", "class_number": "65678",
                  "course_code": "CSE 575", "title": "Statistical Machine Learning", "professor": "Jane Doe"}
    assert ok["confidence"] == "high"
    assert "2 source rows" in ok["notes"]

    no_url = by_file["syllabus-2267-82398.pdf"]
    assert no_url["confidence"] == "low" and no_url["source_url"] == "" and no_url["university"] == ""
    assert "no source URL" in no_url["notes"] and "CSE 573" in no_url["notes"]  # points at the row whose hint matches

    no_prof = by_file["CSE545-Syllabus.pdf"]
    assert no_prof["professor"] == "" and no_prof["confidence"] == "low"
    assert "professor not found" in no_prof["notes"]

    assert problems["pdfs_without_url"] == ["CSE545-Syllabus.pdf", "syllabus-2267-82398.pdf"]
    assert problems["urls_without_pdf"] == [sources[2]["url"], sources[3]["url"]]
    assert problems["duplicate_urls"] == [sources[0]["url"]]


def test_term_code_mismatch_and_hint_mismatch_are_low():
    sources = [{"url": f"{S3}/2261/65678/syllabus-2261-65678.pdf?v=1", "course": "CSE 573: Semantic Web Mining"}]
    rows, _ = build_rows(sources, {"syllabus-2261-65678.pdf": [PAGE_575]})
    row = rows[0]
    assert row["term"] == "Fall 2026" and row["confidence"] == "low"
    assert "term code 2261 = Spring 2026" in row["notes"] and "course hint" in row["notes"]


def test_term_falls_back_to_url_code_with_medium_confidence():
    page = "# CSE 575: Statistical Machine Learning\n**Instructor:** Jane Doe"
    sources = [{"url": f"{S3}/2267/65678/syllabus-2267-65678.pdf?v=1", "course": "CSE 575"}]
    row = build_rows(sources, {"syllabus-2267-65678.pdf": [page]})[0][0]
    assert row["term"] == "Fall 2026" and row["confidence"] == "medium"
    assert "term from URL code" in row["notes"]


# --- professor on later pages ---

@pytest.mark.parametrize("page, name", [
    ("Professor: Dr. Jane Doe", "Jane Doe"),
    ("**Faculty:** Jane Doe, jdoe@example.edu", "Jane Doe"),
    ("Contact: Jane Doe (jdoe@example.edu)", "Jane Doe"),
    ("## Professor\nJane Doe Office: BYENG 592", "Jane Doe"),
    ("INSTRUCTOR INFORMATION\nName: Professor Jane Doe\nEmail address: jdoe@example.edu", "Jane Doe"),   # 75573
    ("Course Creators\nJane Doe, PhD", ""),                                  # 82242/85668: designers, not instructor
    ("Contact: jdoe@example.edu\nInstructor: Jane Doe", "Jane Doe"),
    ("Jane Doe, Associate Professor\nAll classes are in person.", ""),    # "Professor" ends a line, not a label
    ("# 1. Contact Information\nOffice hours by appointment.", ""),
])
def test_find_professor_other_labels(page, name):
    assert find_professor(page) == name


def test_professor_from_later_page_noted_with_medium_confidence():
    sources = [{"url": f"{S3}/2267/65678/syllabus-2267-65678.pdf?v=1", "course": "CSE 575"}]
    page1 = "# CSE 575: Statistical Machine Learning\nFall 2026\n## Course Description"
    row = build_rows(sources, {"syllabus-2267-65678.pdf": [page1, "Grading.", "## Contact\nInstructor: Jane Doe"]})[0][0]
    assert row["professor"] == "Jane Doe" and row["confidence"] == "medium"
    assert "professor from page 3" in row["notes"]


# --- sources.csv file_name column ---

def test_file_name_column_links_url_to_pdf():
    doc_url = "https://docs.google.com/document/d/abc/edit?tab=t.0"
    sources = [{"url": doc_url, "course": "CSE 545: Software Security", "file_name": "CSE545-Syllabus.pdf"},
               {"url": f"{S3}/2267/65678/syllabus-2267-65678.pdf?v=1", "course": "CSE 575", "file_name": ""}]
    docs = {"CSE545-Syllabus.pdf": [PAGE_545], "syllabus-2267-65678.pdf": [PAGE_575]}
    rows, problems = build_rows(sources, docs)
    by_file = {r["file_name"]: r for r in rows}
    assert by_file["CSE545-Syllabus.pdf"]["source_url"] == doc_url
    assert "unknown source host" in by_file["CSE545-Syllabus.pdf"]["notes"]
    assert by_file["syllabus-2267-65678.pdf"]["source_url"] == sources[1]["url"]   # blank file_name: use the URL
    assert problems["pdfs_without_url"] == [] and problems["urls_without_pdf"] == []


# --- overrides ---

def test_overrides_applied_last_and_marked():
    rows = [{"file_name": "CSE545-Syllabus.pdf", "source_url": "", "university": "", "term": "", "class_number": "",
             "course_code": "CSE 545", "title": "Software Security", "professor": "", "confidence": "low",
             "notes": "no source URL"}]
    overrides = [{"file_name": "CSE545-Syllabus.pdf", "professor": "Jane Doe", "university": "ASU", "title": ""},
                 {"file_name": "missing.pdf", "professor": "John Roe"}]
    unmatched = apply_overrides(rows, overrides)
    assert rows[0]["professor"] == "Jane Doe" and rows[0]["university"] == "ASU"
    assert rows[0]["title"] == "Software Security"                   # blank cell = no override
    assert rows[0]["confidence"] == "high"
    assert rows[0]["notes"] == "no source URL; overridden: university, professor"   # manifest column order
    assert unmatched == ["missing.pdf"]


@pytest.mark.parametrize("column", ["confidence", "notes", "colour"])
def test_overrides_reject_unknown_or_derived_columns(column):
    with pytest.raises(ValueError, match=column):
        apply_overrides([], [{"file_name": "a.pdf", column: "x"}])
