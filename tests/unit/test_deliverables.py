"""Stdlib document helpers and structural checks for generic-task deliverables."""

from __future__ import annotations

import io
import zipfile

import pytest

from packages.common import deliverables as d

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DOC_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:document xmlns:w="{W}" xmlns:mc="http://schemas.openxmlformats.org/'
    'markup-compatibility/2006" xmlns:w14="http://schemas.microsoft.com/office/word/2010/'
    'wordml" mc:Ignorable="w14"><w:body>'
    '<w:p w14:paraId="1"><w:pPr><w:pStyle w:val="Title"/></w:pPr>'
    '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">Hello </w:t></w:r>'
    "<w:r><w:t>world &amp; friends</w:t></w:r></w:p>"
    "<w:p/>"
    "<w:p><w:r><w:t></w:t></w:r></w:p>"
    "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell text</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    "<w:p><w:r><w:t>Last paragraph</w:t></w:r></w:p>"
    "</w:body></w:document>"
)


def make_docx(xml: str = DOC_XML) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", xml)
        zf.writestr("word/styles.xml", "<w:styles/>")
    return buf.getvalue()


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def make_xlsx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>'
            '<sheet name="Sales" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="{NS}"><si><t>region</t></si><si><t>value</t></si>'
            "<si><t>北</t></si></sst>",
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{NS}"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="C2"><v>3.5</v></c></row>'
            '<row r="3"><c r="A3" t="inlineStr"><is><t>南</t></is></c><c r="B3"><v>2</v></c></row>'
            "</sheetData></worksheet>",
        )
    return buf.getvalue()


def test_docx_paragraphs_in_order_including_tables():
    assert d.docx_paragraphs(make_docx()) == [
        "Hello world & friends",
        "Cell text",
        "Last paragraph",
    ]


def test_docx_replace_keeps_package_formatting_and_escapes():
    out = d.docx_replace_paragraphs(make_docx(), ["こんにちは <世界>", "セル", "最後"])
    assert d.docx_paragraphs(out) == ["こんにちは <世界>", "セル", "最後"]
    zf = zipfile.ZipFile(io.BytesIO(out))
    assert zf.read("word/styles.xml") == b"<w:styles/>"
    xml = zf.read("word/document.xml").decode()
    assert "<w:b/>" in xml and 'mc:Ignorable="w14"' in xml and "&lt;世界&gt;" in xml
    assert '<w:t xml:space="preserve">セル' in xml
    with pytest.raises(d.DocumentError, match="fewer"):
        d.docx_replace_paragraphs(make_docx(), ["only one"])
    with pytest.raises(d.DocumentError, match="more"):
        d.docx_replace_paragraphs(make_docx(), ["a", "b", "c", "d"])


def test_minimal_docx_and_text_rendering():
    doc = d.docx_from_paragraphs(["Eins", "Zwei & drei"])
    assert d.docx_paragraphs(doc) == ["Eins", "Zwei & drei"]
    assert (
        d.render_translation("a.md", b"# A\n\nB\n", ["# Ä", "Ö"], "md")
        == b"# \xc3\x84\n\n\xc3\x96\n"
    )
    assert d.text_paragraphs(b"# Title\r\n\r\nPara one\nline two\n\n\n  \nPara two") == [
        "# Title",
        "Para one\nline two",
        "Para two",
    ]


@pytest.mark.parametrize(
    ("data", "match"),
    [
        (b"not a zip", "ZIP"),
        (make_docx('<!DOCTYPE x [<!ENTITY a "b">]><w:document/>'), "DTD"),
        (
            d.docx_from_paragraphs(["x"]).replace(b"word/document.xml", b"word/documenX.xml"),
            "missing",
        ),
    ],
)
def test_malformed_documents_are_refused(data, match):
    with pytest.raises((d.DocumentError, UnicodeError), match=match):
        d.docx_paragraphs(data)


def test_xlsx_sheets_shared_inline_and_gaps():
    assert d.xlsx_sheets(make_xlsx()) == {
        "Sales": [["region", "value"], ["北", "", "3.5"], ["南", "2"]]
    }
    assert d.rows_to_csv([["a", "b,c"], ["1"]]) == b'a,"b,c"\n1\n'


def test_script_ratio_and_safe_names():
    assert d.script_ratio("これは翻訳です", "ja") == 1.0
    assert d.script_ratio("Hello world", "ja") == 0.0
    assert d.script_ratio("Bonjour à tous", "fr") == 1.0
    assert d.script_ratio("whatever", "xx") is None
    assert d.safe_name("报告 v1.docx") == "v1.docx"
    assert d.safe_name("../../etc/passwd") == "file.etcpassw"  # flat, never a path
    assert d.safe_name("Chart 1.PNG") == "Chart-1.png"
    assert d.safe_name("-x.md") == "x.md"


def test_png_and_svg_checks():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (64).to_bytes(4, "big") * 2
    assert d.png_size(png) == (64, 64)
    with pytest.raises(d.DocumentError):
        d.png_size(b"GIF89a")
    d.check_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="1"/></svg>')
    for bad in (
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="x"/>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:x"/></svg>',
        b'<!DOCTYPE svg [<!ENTITY a "b">]><svg/>',
        b"<html/>",
    ):
        with pytest.raises(d.DocumentError):
            d.check_svg(bad)


SPEC = {"format": "docx", "filename": "translation.docx"}


def translation(texts):
    return [d.FileIn("translation.docx", d.docx_replace_paragraphs(make_docx(), texts))]


def test_translation_check_passes_and_reports_each_failure():
    src = d.FileIn("paper.docx", make_docx())
    good = d.check_translation(translation(["こんにちは", "セル", "最後の段落"]), src, "ja", SPEC)
    assert good.ok, good.checks
    english = d.check_translation(
        translation(["Hello world & friends", "Cell text", "Last paragraph"]), src, "ja", SPEC
    )
    assert english.reason == "WRONG_LANGUAGE"
    same = d.check_translation(
        translation(["Hello world & friends", "Cell text", "Last paragraph"]), src, "en", SPEC
    )
    assert same.reason == "NOT_TRANSLATED"
    missing = d.check_translation([d.FileIn("other.docx", b"x")], src, "ja", SPEC)
    assert missing.reason == "MISSING_OUTPUT"
    broken = d.check_translation([d.FileIn("translation.docx", b"zip?")], src, "ja", SPEC)
    assert broken.reason == "MALFORMED_OUTPUT"
    short = d.check_translation(
        [d.FileIn("translation.md", "これだけ\n".encode())],
        d.FileIn("a.md", b"one\n\ntwo\n\nthree\n\nfour\n\nfive\n"),
        "ja",
        {"format": "md", "filename": "translation.md"},
    )
    assert short.reason == "PARAGRAPH_COUNT_MISMATCH"
    wrong_type = d.check_translation([d.FileIn("translation.exe", b"x")], src, "ja", SPEC)
    assert wrong_type.reason == "FILE_TYPE_NOT_ALLOWED"


def test_analysis_check():
    report = ("# Analysis of sales.xlsx\n\n" + "Mean value 2.0 = 6 / 3. " * 12).encode()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    ok = d.check_analysis(
        [d.FileIn("report.md", report), d.FileIn("chart-1.svg", svg)], ["sales.xlsx"]
    )
    assert ok.ok, ok.checks
    assert d.check_analysis([d.FileIn("chart-1.svg", svg)], ["s.csv"]).reason == "MISSING_OUTPUT"
    assert (
        d.check_analysis([d.FileIn("report.md", b"too short")], ["s.csv"]).reason
        == "REPORT_TOO_SHORT"
    )
    assert (
        d.check_analysis([d.FileIn("report.md", report)], ["other.csv"]).reason
        == "REPORT_DOES_NOT_CITE_INPUTS"
    )
    bad_chart = d.check_analysis(
        [d.FileIn("report.md", report), d.FileIn("chart-1.png", b"nope")], ["sales.xlsx"]
    )
    assert bad_chart.reason == "MALFORMED_OUTPUT"
    big = d.check_analysis(
        [d.FileIn("report.md", report), d.FileIn("a.csv", b"x" * (d.MAX_DELIVERABLE_BYTES + 1))],
        ["sales.xlsx"],
    )
    assert big.reason == "FILE_TOO_LARGE"


def test_translation_json_parsing():
    assert d.parse_translation_json(b'["a","b"]', 2) == ["a", "b"]
    for raw in (b"{}", b'["a"]', b"[1, 2]", b"nope"):
        with pytest.raises(d.DocumentError):
            d.parse_translation_json(raw, 2)
