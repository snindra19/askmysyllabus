"""Conversion-stage cleaner (DESIGN.md 11.3). Cases come from the converter bake-off."""

import pytest

from ingest.convert import dash_pairs, is_page_number, remove_repeats, repair_dashes, strip_html, vocabulary


# --- page numbers: every format pymupdf4llm produced in the bake-off ---

@pytest.mark.parametrize("line", ["3", "12 ", "3 of 13", "page 3 of 4", "Page | 3", "**7**"])
def test_page_number_formats(line):
    assert is_page_number(line)


@pytest.mark.parametrize("line", ["2. Quizzes:", "Total 100%", "Week 3", "| 3 | Exam |", "Homework 5 5%"])
def test_not_page_numbers(line):
    assert not is_page_number(line)


# --- margin repeats ---

def test_course_title_heading_kept_running_header_removed():
    # 74856: title heading on page 1, same text as a running header on later pages.
    title = "CSE 494/559: Algorithms in Computational Biology"
    pages = [
        f"# {title}\n\nFall 2026\n\nIntro text.\n\npage 1 of 4\n",
        f"{title}\n\nFall 2026\n\nSchedule text.\n\npage 2 of 4\n",
        f"{title}\n\nFall 2026\n\nGrading text.\n\npage 3 of 4\n",
    ]
    out, log = remove_repeats(pages)
    assert out[0].startswith(f"# {title}")
    assert title not in out[1] and title not in out[2]
    assert "Fall 2026" in out[0] and "Fall 2026" not in out[1] + out[2]
    assert "Schedule text." in out[1] and "Grading text." in out[2]
    assert not any("page" in p.lower() and "of 4" in p for p in out)
    assert {e["rule"] for e in log} == {"margin repeat", "page number"}
    assert all({"page", "rule", "text"} <= e.keys() for e in log)


def test_table_rows_at_page_edges_kept():
    sep = "|---|---|---|---|"
    pages = [
        f"Intro.\n\n| Week | Topic | Due | Notes |\n{sep}\n| 1 | A | - | - |\n",
        f"{sep}\n| 2 | B | - | - |\n\nMore text.\n",
        f"{sep}\n| 3 | C | - | - |\n\nEnd.\n",
    ]
    out, log = remove_repeats(pages)
    assert all(sep in p for p in out)
    assert log == []


def test_edge_lines_differing_only_in_numbers_kept():
    # 82242: list items "Module 3 ..." and "Module 5 ..." each sat at a page edge.
    pages = ["- **Module 3 Graded Discussion Prompt**\n\nText a.\n", "- **Module 5 Graded Discussion Prompt**\n\nText b.\n"]
    out, log = remove_repeats(pages)
    assert out == [p.rstrip("\n") for p in pages] and log == []


def test_page_number_removed_anywhere_but_not_in_tables():
    out, log = remove_repeats(["Text.\n\n5\n\nMore text.\n", "| 5 |\n"])
    assert "\n5\n" not in out[0] and "| 5 |" in out[1]
    assert log == [{"page": 1, "rule": "page number", "text": "5"}]


# --- HTML tags ---

def test_html_tags_replaced_with_space():
    assert strip_html("Aug 23<sup>rd</sup>, 2026") == "Aug 23 rd , 2026"
    assert strip_html("Schools of<br>Engineering<!-- End of picture text -->") == "Schools of Engineering "
    assert strip_html("<u>Exams</u> if a < b and c > d") == " Exams  if a < b and c > d"
    assert strip_html("see <https://example.edu>") == "see <https://example.edu>"


# --- dash repair ---

def test_dash_pairs_from_raw_text():
    pages = ["Students living off-\ncampus must\nACD 304-\n06 applies", "large-\nscale data"]
    assert dash_pairs(pages) == [(1, "off", "-", "campus"), (1, "304", "-", "06"), (2, "large", "-", "scale")]


def test_wrong_merge_restored():
    text, log = repair_dashes("Living offcampus is fine.", [(2, "off", "-", "campus")], set())
    assert text == "Living off-campus is fine."
    assert log == [{"page": 2, "rule": "dash", "pair": "off-campus", "decision": "restored hyphen", "count": 1}]


def test_large_scale_restored_and_kept_when_converter_kept_it():
    assert repair_dashes("a largescale system", [(1, "large", "-", "scale")], set())[0] == "a large-scale system"
    text, log = repair_dashes("a large-scale system", [(1, "large", "-", "scale")], set())
    assert text == "a large-scale system" and log[0]["decision"] == "converter kept dash"


@pytest.mark.parametrize("dash", ["-", "–"])
def test_digits_keep_dash(dash):
    text, log = repair_dashes("see ACD 30406 now", [(4, "304", dash, "06")], {"30406"})
    assert text == f"see ACD 304{dash}06 now" and log[0]["decision"] == "restored dash (digits)"


def test_joined_form_found_next_to_emphasis_markers():
    # 74856: "_... Approach_" (italic) after the converter joined "Ap-proach".
    log = repair_dashes("_A Bottom-up Approach_ ,", [(1, "Ap", "-", "proach")], set())[1]
    assert log[0]["count"] == 1 and log[0]["decision"] == "joined (common word)"
    assert repair_dashes("_living offcampus_", [(1, "off", "-", "campus")], set())[0] == "_living off-campus_"


def test_common_word_stays_joined():
    text, log = repair_dashes("All students attend.", [(1, "stu", "-", "dents")], set())
    assert text == "All students attend." and log[0]["decision"] == "joined (common word)"


def test_word_elsewhere_in_corpus_stays_joined():
    text, log = repair_dashes("uses multithreading", [(1, "multi", "-", "threading")], {"multithreading"})
    assert text == "uses multithreading" and log[0]["decision"] == "joined (in corpus)"


@pytest.mark.parametrize("text, pair", [
    ("ACD 304-08 applies", (1, "304", "–", "08")),
    ("ACD 304–08 applies", (1, "304", "–", "08")),
    ("uses map- reduction", (1, "map", "-", "reduction")),
    ("uses _map- reduction_ here", (1, "map", "-", "reduction")),
])
def test_kept_dash_detected_with_other_dash_or_spaces(text, pair):
    out, log = repair_dashes(text, [pair], set())
    assert out == text and log[0]["decision"] == "converter kept dash"


def test_vocabulary_skips_urls_and_glued_mixed_case():
    # 86840 raw text has "UniversitySanctioned"; CSE535 has "academicintegrity" only inside a URL.
    words = vocabulary(["Due to UniversitySanctioned Activities", "see http://provost.example.edu/academicintegrity now",
                        "Multithreading and multithreading, MULTITHREADING"])
    assert "universitysanctioned" not in words and "academicintegrity" not in words
    assert {"due", "activities", "see", "now", "multithreading"} <= words


def test_each_pair_handled_once():
    pairs = [(1, "off", "-", "campus"), (3, "off", "-", "campus")]
    text, log = repair_dashes("offcampus and offcampus", pairs, set())
    assert text == "off-campus and off-campus" and len(log) == 1 and log[0]["count"] == 2
