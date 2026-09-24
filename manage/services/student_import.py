"""Read the existing student template without evaluating formulas or writing files.

Contract: one worksheet; A1:C1 = 学号/姓名/性别; rows 2–1001 contain
text cells. D and later columns are ignored, including the legacy E-column
concatenation formulas. Student-number uniqueness and business validation stay
in roster.change_roster's preview/commit flow.
"""
from io import BytesIO
from pathlib import PurePosixPath
import re
from xml.etree import ElementTree
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile
from zlib import error as ZlibError

from .errors import BusinessError


MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_ZIP_ENTRIES = 128
MAX_ROWS = 1000
MAX_SHARED_STRINGS = 10000
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOCUMENT_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"m": MAIN, "r": DOCUMENT_REL}
CELL_REFERENCE = re.compile(r"([A-Z]{1,3})([1-9][0-9]{0,6})\Z")


def _invalid(message="Excel 文件格式无效，请使用下载的 .xlsx 模板。", **details):
    return BusinessError("invalid_workbook", message, details=details)


def _package_path(path):
    # Never extract ZIP entries or accept ambiguous traversal/URL targets.
    if not path or "\\" in path or ":" in path or "\x00" in path:
        raise _invalid()
    parts = path.split("/")
    if path.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise _invalid()
    return str(PurePosixPath(path))


def _xml(archive, path, expected_tag):
    try:
        info = archive.getinfo(path)
    except KeyError:
        raise _invalid() from None
    if info.file_size > MAX_XML_BYTES:
        raise BusinessError("workbook_too_large", "Excel 内部数据过大，请只保留新增名单。")
    with archive.open(info) as entry:
        raw = entry.read(MAX_XML_BYTES + 1)
    if len(raw) > MAX_XML_BYTES:
        raise BusinessError("workbook_too_large", "Excel 内部数据过大，请只保留新增名单。")
    # UTF-8-only XML makes the DTD/entity rejection unambiguous, including BOMs.
    # Neither external resources nor formulas are evaluated anywhere in this module.
    source = raw.decode("utf-8-sig")
    if "\x00" in source or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", source, re.I):
        raise _invalid("Excel 不支持包含实体或外部文档声明的 XML。")
    root = ElementTree.fromstring(source)
    if root.tag != expected_tag:
        raise _invalid()
    return root


def _relationships(archive, path, base):
    result = {}
    root = _xml(archive, path, f"{{{PACKAGE_REL}}}Relationships")
    for relation in root:
        key, kind = relation.get("Id"), relation.get("Type", "")
        target = relation.get("Target", "")
        if (relation.tag != f"{{{PACKAGE_REL}}}Relationship" or not key or key in result
                or relation.get("TargetMode", "Internal") != "Internal"):
            raise _invalid("Excel 工作簿关系无效，不支持外部工作簿。")
        target = _package_path(target.lstrip("/") if target.startswith("/") else base + target)
        result[key] = (kind, target)
    return result


def _text_element(element):
    if element is None:
        return ""
    # Only text/rich-text runs count; phonetic annotations are not name characters.
    return "".join((node.text or "") for node in element.findall("m:t", NS)) + "".join(
        (node.text or "") for node in element.findall("m:r/m:t", NS))


def _cell_text(cell, shared_strings, row, column):
    if cell is None:
        return ""
    if cell.find("m:f", NS) is not None:
        raise BusinessError("workbook_formula", "学号、姓名和性别列不支持公式，请填写原始文本。",
                            details={"row": row, "column": column})
    kind = cell.get("t", "n")
    value = cell.find("m:v", NS)
    if kind == "inlineStr":
        text = _text_element(cell.find("m:is", NS))
    elif value is None or value.text is None:
        return ""
    elif kind == "s":
        try:
            index = int(value.text)
            if index < 0:
                raise ValueError
            text = shared_strings[index]
        except (ValueError, IndexError):
            raise _invalid() from None
    elif kind == "str":
        text = value.text
    else:
        if column == "A":
            # A numeric value cannot prove whether Excel already discarded zeros
            # or rounded beyond 15 significant digits, even with a text style.
            raise BusinessError("workbook_numeric_student_number",
                "学号必须是文本。请核对原始学号，以文本重新输入，避免前导零或长学号丢失。",
                details={"row": row, "column": column})
        raise _invalid("姓名和性别必须填写文本。", row=row, column=column)
    text = text.strip()
    if "|" in text or any(character in text for character in "\r\n\v\f\x85\u2028\u2029"):
        raise _invalid("单元格不能包含竖线或换行。", row=row, column=column)
    return text


def _parse_archive(archive):
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_ENTRIES or sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
        raise BusinessError("workbook_too_large", "Excel 解压后过大，请只保留新增名单。")
    seen = set()
    for info in infos:
        name = _package_path(info.filename.rstrip("/") if info.is_dir() else info.filename)
        if name in seen or info.flag_bits & 1 or info.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
            raise _invalid()
        seen.add(name)
    content_types = _xml(archive, "[Content_Types].xml", f"{{{CONTENT_TYPES}}}Types")
    if any("macroenabled" in item.get("ContentType", "").lower()
           or "vbaproject" in item.get("ContentType", "").lower() for item in content_types):
        raise _invalid("只支持不含宏的 .xlsx 文件。")
    root_relationships = _relationships(archive, "_rels/.rels", "")
    documents = [target for kind, target in root_relationships.values() if kind == DOCUMENT_REL + "/officeDocument"]
    if len(documents) != 1:
        raise _invalid()
    workbook_path = documents[0]
    workbook = _xml(archive, workbook_path, f"{{{MAIN}}}workbook")
    parent = str(PurePosixPath(workbook_path).parent)
    base = "" if parent == "." else parent + "/"
    relationships = _relationships(archive, base + "_rels/" + PurePosixPath(workbook_path).name + ".rels", base)
    sheets = workbook.findall("m:sheets/m:sheet", NS)
    if len(sheets) != 1:
        raise _invalid("请仅保留一张名单工作表，避免遗漏其他表中的学生。")
    relation = relationships.get(sheets[0].get(f"{{{DOCUMENT_REL}}}id"))
    if not relation or relation[0] != DOCUMENT_REL + "/worksheet":
        raise _invalid()
    worksheet = _xml(archive, relation[1], f"{{{MAIN}}}worksheet")
    string_paths = [target for kind, target in relationships.values() if kind == DOCUMENT_REL + "/sharedStrings"]
    if len(string_paths) > 1:
        raise _invalid()
    shared_strings = []
    if string_paths:
        strings = _xml(archive, string_paths[0], f"{{{MAIN}}}sst")
        if len(strings) > MAX_SHARED_STRINGS:
            raise BusinessError("workbook_too_large", "Excel 文本条目过多，请只保留新增名单。")
        shared_strings = [_text_element(item) for item in strings]
    rows = worksheet.findall("m:sheetData/m:row", NS)
    if len(rows) > MAX_ROWS + 1:
        raise BusinessError("workbook_row_limit", "每次最多新增1000名学生，请保留表头及第2–1001行。")
    previous_row = 0
    header = None
    students = []
    for row in rows:
        raw_row = row.get("r", "")
        if not re.fullmatch(r"[1-9][0-9]{0,6}", raw_row):
            raise _invalid()
        row_number = int(raw_row)
        if row_number > MAX_ROWS + 1:
            raise BusinessError("workbook_row_limit", "每次最多新增1000名学生，请保留表头及第2–1001行。")
        if row_number <= previous_row:
            raise _invalid()
        previous_row = row_number
        cells = {}
        for cell in row.findall("m:c", NS):
            match = CELL_REFERENCE.fullmatch(cell.get("r", ""))
            if not match or int(match[2]) != row_number:
                raise _invalid()
            column = match[1]
            if column in cells:
                raise _invalid()
            cells[column] = cell
        values = [_cell_text(cells.get(column), shared_strings, row_number, column) for column in "ABC"]
        if row_number == 1:
            header = values
            continue
        if not any(values):
            continue
        number, name, sex = values
        sex = {"male": "男", "female": "女"}.get(sex, sex)
        if sex not in ("男", "女"):
            raise _invalid("性别请填写男或女。", row=row_number, column="C")
        students.append(f"{number}|{name}|{sex}")
    if header != ["学号", "姓名", "性别"]:
        raise _invalid("模板第1行的前三列必须依次为学号、姓名、性别。")
    if not students:
        raise _invalid("请在模板中填写1–1000名学生后上传。")
    return {"students_text": "\n".join(students), "count": len(students)}


def parse_student_workbook(upload):
    """Return canonical text for roster preview; never persist imported students.

    The existing template's six example rows are ordinary rows: the UI must tell
    users to replace them and show the complete preview before confirmation.
    """
    if not upload or not str(getattr(upload, "name", "")).lower().endswith(".xlsx"):
        raise _invalid("请选择 .xlsx 格式的新增学生模板。")
    size = getattr(upload, "size", None)
    if isinstance(size, int) and size > MAX_UPLOAD_BYTES:
        raise BusinessError("workbook_too_large", "Excel 文件不能超过2 MiB。")
    try:
        upload.seek(0)
        payload = upload.read(MAX_UPLOAD_BYTES + 1)
        if not isinstance(payload, bytes):
            raise _invalid()
        if len(payload) > MAX_UPLOAD_BYTES:
            raise BusinessError("workbook_too_large", "Excel 文件不能超过2 MiB。")
        with ZipFile(BytesIO(payload)) as archive:
            return _parse_archive(archive)
    except (BadZipFile, ZlibError, ElementTree.ParseError, UnicodeDecodeError,
            OSError, ValueError, RuntimeError, EOFError):
        raise _invalid() from None
