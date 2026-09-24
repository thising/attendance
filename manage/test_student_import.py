"""Synthetic OOXML files verify the upload boundary without touching a database."""
from io import BytesIO
import struct
from unittest import mock
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from manage.services.errors import BusinessError
from manage.services.student_import import (
    MAX_ROWS, MAX_UNCOMPRESSED_BYTES, MAX_UPLOAD_BYTES, MAX_XML_BYTES,
    parse_student_workbook,
)


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"


def text_cell(reference, value):
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'


def record(row, number="001", name="合成姓名", sex="男", extra=""):
    cells = "".join(text_cell(f"{column}{row}", value) for column, value in zip("ABC", (number, name, sex)))
    return f'<row r="{row}">{cells}{extra}</row>'


def workbook(rows=None, *, overrides=None, extra_entries=None, second_sheet=False, name="students.xlsx"):
    if rows is None:
        rows = record(2)
    sheet = record(1, "学号", "姓名", "性别") + rows
    files = {
        "[Content_Types].xml": f'<Types xmlns="{TYPES}"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>',
        "_rels/.rels": f'<Relationships xmlns="{PKG_REL}"><Relationship Id="book" Type="{DOC_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'<workbook xmlns="{MAIN}" xmlns:r="{DOC_REL}"><sheets><sheet name="名单" sheetId="1" r:id="sheet"/>' + ('<sheet name="第二张" sheetId="2" r:id="other"/>' if second_sheet else '') + '</sheets></workbook>',
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{PKG_REL}"><Relationship Id="sheet" Type="{DOC_REL}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{MAIN}"><sheetData>{sheet}</sheetData></worksheet>',
    }
    files.update(overrides or {})
    files.update(extra_entries or {})
    content = BytesIO()
    with ZipFile(content, "w", compression=ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return SimpleUploadedFile(name, content.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class StudentWorkbookTests(SimpleTestCase):
    def assert_rejected(self, upload, code="invalid_workbook", details=None):
        with self.assertRaises(BusinessError) as raised:
            parse_student_workbook(upload)
        self.assertEqual(raised.exception.code, code)
        if details is not None:
            self.assertEqual(raised.exception.details, details)
        return raised.exception

    def test_text_ids_preserve_leading_zero_and_long_precision_with_canonical_sex(self):
        upload = workbook(record(2, " 00012345678901234567 ", " 合成甲 ", "female")
                          + record(3, "002", "合成乙", "male"))
        self.assertEqual(parse_student_workbook(upload), {
            "students_text": "00012345678901234567|合成甲|女\n002|合成乙|男", "count": 2,
        })

    def test_shared_strings_rich_text_and_phonetics(self):
        strings = '<si><t>00001</t></si><si><r><t>合成</t></r><r><t>甲</t></r><rPh sb="0" eb="1"><t>ignored</t></rPh></si><si><t>女</t></si>'
        rows = '<row r="2"><c r="A2" t="s"><v>0</v></c><c r="B2" t="s"><v>1</v></c><c r="C2" t="s"><v>2</v></c></row>'
        upload = workbook(rows, overrides={
            "xl/sharedStrings.xml": f'<sst xmlns="{MAIN}">{strings}</sst>',
            "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{PKG_REL}"><Relationship Id="sheet" Type="{DOC_REL}/worksheet" Target="/xl/worksheets/sheet1.xml"/><Relationship Id="strings" Type="{DOC_REL}/sharedStrings" Target="sharedStrings.xml"/></Relationships>',
        })
        self.assertEqual(parse_student_workbook(upload), {"students_text": "00001|合成甲|女", "count": 1})

    def test_legacy_helper_formulas_and_stale_cached_values_are_ignored(self):
        helper = '<c r="E2" t="str"><f>IF(LEN(A2)&gt;0,A2&amp;"|"&amp;B2&amp;"|"&amp;C2,"")</f><v>stale|ignored|女</v></c>'
        helper_only = '<row r="3"><c r="E3" t="str"><f t="shared" si="0"/><v>must-not-import</v></c></row>'
        upload = workbook(record(2, extra=helper) + helper_only + '<row r="200"><c r="A200" s="6"/></row>')
        self.assertEqual(parse_student_workbook(upload), {"students_text": "001|合成姓名|男", "count": 1})

    def test_formula_in_each_input_column_is_rejected_even_without_cached_value(self):
        for column in "ABC":
            with self.subTest(column=column):
                cells = "".join(f'<c r="{c}2"><f t="shared" si="0"/></c>' if c == column
                                else text_cell(f"{c}2", value) for c, value in zip("ABC", ("001", "合成甲", "男")))
                self.assert_rejected(workbook(f'<row r="2">{cells}</row>'), "workbook_formula",
                                     {"row": 2, "column": column})

    def test_all_numeric_ids_are_rejected_including_text_style_and_long_rounded_values(self):
        for raw in ("123", "12345678901234500", "1.23456789012345E+17", "1.5", "00123"):
            with self.subTest(raw=raw):
                cells = f'<c r="A2" s="49"><v>{raw}</v></c>' + text_cell("B2", "合成甲") + text_cell("C2", "男")
                self.assert_rejected(workbook(f'<row r="2">{cells}</row>'), "workbook_numeric_student_number")

    def test_header_order_is_exact_and_multiple_sheets_are_not_silently_ignored(self):
        self.assert_rejected(workbook(second_sheet=True))
        sheet = f'<worksheet xmlns="{MAIN}"><sheetData>{record(1, "姓名", "学号", "性别")}{record(2)}</sheetData></worksheet>'
        self.assert_rejected(workbook(overrides={"xl/worksheets/sheet1.xml": sheet}))

    def test_empty_rows_are_skipped_but_incomplete_rows_reach_roster_validation(self):
        result = parse_student_workbook(workbook(record(2, "", "", "") + record(3, "001", "", "女")))
        self.assertEqual(result, {"students_text": "001||女", "count": 1})
        self.assert_rejected(workbook(record(2, "", "", "")))

    def test_duplicate_ids_and_business_lengths_remain_for_whole_batch_roster_preview(self):
        long_name = "长" * 21
        result = parse_student_workbook(workbook(record(2, name=long_name) + record(3)))
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["students_text"].splitlines()[0], "001|" + long_name + "|男")

    def test_line_or_column_injection_and_invalid_sex_are_rejected_without_echoing_values(self):
        for name in ("保密|内容", "保密\n内容", "保密\u2028内容"):
            with self.subTest(name=name):
                failure = self.assert_rejected(workbook(record(2, name=name)))
                self.assertNotIn("保密", str(failure))
                self.assertEqual(failure.details, {"row": 2, "column": "B"})
        self.assert_rejected(workbook(record(2, sex="other")))

    def test_maximum_student_rows_are_accepted_and_extra_or_sparse_rows_rejected(self):
        upload = workbook("".join(record(row, number=str(row)) for row in range(2, MAX_ROWS + 2)))
        self.assertEqual(parse_student_workbook(upload)["count"], MAX_ROWS)
        self.assert_rejected(workbook("".join(record(row) for row in range(2, MAX_ROWS + 3))), "workbook_row_limit")
        self.assert_rejected(workbook(record(1048576)), "workbook_row_limit")

    def test_upload_byte_limit_is_enforced_even_when_size_metadata_lies(self):
        upload = SimpleUploadedFile("students.xlsx", b"x" * (MAX_UPLOAD_BYTES + 1))
        self.assert_rejected(upload, "workbook_too_large")
        upload.size = 1
        self.assert_rejected(upload, "workbook_too_large")

    def test_decompressed_limit_rejects_zip_bomb_before_reading_xml(self):
        upload = workbook(extra_entries={"unused.bin": b"0" * MAX_UNCOMPRESSED_BYTES})
        self.assertLess(upload.size, MAX_UPLOAD_BYTES)
        with mock.patch("manage.services.student_import._xml", side_effect=AssertionError("must check ZIP limits first")):
            self.assert_rejected(upload, "workbook_too_large")

    def test_per_xml_size_and_entry_count_are_bounded(self):
        self.assert_rejected(workbook(overrides={"[Content_Types].xml": b" " * (MAX_XML_BYTES + 1)}), "workbook_too_large")
        self.assert_rejected(workbook(extra_entries={f"unused/{i}": "" for i in range(129)}), "workbook_too_large")

    def test_archive_traversal_and_external_workbook_relationship_are_rejected(self):
        for path in ("../outside", "/outside", "xl/../outside", "C:/outside", "xl\\outside"):
            with self.subTest(path=path):
                self.assert_rejected(workbook(extra_entries={path: ""}))
        external = f'<Relationships xmlns="{PKG_REL}"><Relationship Id="sheet" Type="{DOC_REL}/worksheet" Target="https://example.invalid/worksheet.xml" TargetMode="External"/></Relationships>'
        self.assert_rejected(workbook(overrides={"xl/_rels/workbook.xml.rels": external}))

    def test_entity_declarations_and_utf16_cannot_bypass_xml_boundary(self):
        xml = f'<!DOCTYPE worksheet [<!ENTITY x "expanded">]><worksheet xmlns="{MAIN}"><sheetData>&x;</sheetData></worksheet>'
        self.assert_rejected(workbook(overrides={"xl/worksheets/sheet1.xml": xml}))
        self.assert_rejected(workbook(overrides={"xl/worksheets/sheet1.xml": xml.encode("utf-16")}))

    def test_bad_format_macros_and_corrupt_shared_string_are_rejected(self):
        self.assert_rejected(SimpleUploadedFile("students.xlsx", b"not a zip"))
        self.assert_rejected(workbook(name="students.xls"))
        self.assert_rejected(workbook(overrides={"[Content_Types].xml": f'<Types xmlns="{TYPES}"><Override ContentType="application/vnd.ms-excel.sheet.macroEnabled.main+xml"/></Types>'}))
        self.assert_rejected(workbook('<row r="2"><c r="A2" t="s"><v>-1</v></c></row>'))

    def test_duplicate_cell_and_out_of_order_rows_are_rejected(self):
        self.assert_rejected(workbook(record(2, extra=text_cell("A2", "different"))))
        self.assert_rejected(workbook(record(3) + record(2)))

    def test_damaged_deflate_stream_returns_a_business_error(self):
        content = bytearray(workbook().file.getvalue())
        with ZipFile(BytesIO(content)) as archive:
            offset = archive.getinfo("xl/worksheets/sheet1.xml").header_offset
        name_length, extra_length = struct.unpack_from("<HH", content, offset + 26)
        content[offset + 30 + name_length + extra_length] = 0x07  # Reserved DEFLATE block type.
        self.assert_rejected(SimpleUploadedFile("students.xlsx", bytes(content)))

    def test_upload_is_read_only_and_does_not_write_database_or_files(self):
        upload = workbook()
        original = upload.file.getvalue()
        with mock.patch("builtins.open", side_effect=AssertionError("no filesystem I/O")):
            self.assertEqual(parse_student_workbook(upload)["count"], 1)
        self.assertEqual(upload.file.getvalue(), original)
        self.assertEqual(parse_student_workbook(upload)["count"], 1)
