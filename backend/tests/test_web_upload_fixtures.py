# -*- coding: utf-8 -*-
"""上传夹具回归测试。

复核意见 4：第一版的 ``Buffer.alloc(2048, 0x41)`` 命名成 ``.xlsx`` 只是"带扩展名的字节"，
不是有效工作簿。这里锁定：

- "合法文件"样本必须是**结构有效**的最小 XLSX（zipfile 可解析、必需部件齐全、XML 合法）；
- 浏览器侧的 STORED 拼装开销口径必须与 Python 一致（否则字节精确样本会算错）；
- 扩展名不合法样本单独构造，不与合法样本混用。
"""

from __future__ import annotations

import base64
import io
import struct

import pytest
import xml.etree.ElementTree as ET
import zipfile

from app.services.web_upload_fixtures import (
    XLSX_PARTS,
    bad_extension_bytes,
    minimal_xls_bytes,
    minimal_xlsx_bytes,
    xls_base64,
    xlsx_parts_base64,
    xlsx_stored_overhead,
)


class TestMinimalWorkbook:
    def test_is_a_readable_zip_with_required_parts(self):
        data = minimal_xlsx_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zf.testzip() is None
            names = set(zf.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                "xl/worksheets/sheet1.xml"} <= names

    def test_parts_are_well_formed_xml(self):
        for name, payload in XLSX_PARTS.items():
            ET.fromstring(payload.decode("utf-8"))     # 语法错误会直接抛异常

    def test_workbook_points_at_the_sheet(self):
        data = minimal_xlsx_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            workbook = zf.read("xl/workbook.xml").decode("utf-8")
            rels = zf.read("xl/_rels/workbook.xml.rels").decode("utf-8")
            sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert 'r:id="rId1"' in workbook
        assert 'Id="rId1"' in rels and "worksheets/sheet1.xml" in rels
        assert "手机号" in sheet                        # 有真实单元格内容

    def test_parts_base64_round_trip(self):
        import base64

        for name, b64 in xlsx_parts_base64().items():
            assert base64.b64decode(b64) == XLSX_PARTS[name]

    def test_stored_overhead_matches_zip_writer_accounting(self):
        """STORED 拼装开销 = 各部件(本地头30+名+数据) + 中央目录(46+名) + EOCD 22。"""
        expected = 22
        for name, payload in XLSX_PARTS.items():
            name_len = len(name.encode("utf-8"))
            expected += 30 + name_len + len(payload) + 46 + name_len
        assert xlsx_stored_overhead() == expected


class TestBadExtensionSample:
    def test_has_executable_header_and_requested_size(self):
        data = bad_extension_bytes(256)
        assert data.startswith(b"MZ")
        assert len(data) == 256

    def test_is_not_a_zip_so_it_cannot_be_confused_with_a_workbook(self):
        data = bad_extension_bytes(1024)
        assert not zipfile.is_zipfile(io.BytesIO(data))


class TestMinimalXls:
    """复核要求覆盖 .xls：样本必须是**有效**文件，而非"带扩展名的字节"。"""

    def test_can_be_parsed_by_a_real_reader(self):
        xlrd = pytest.importorskip("xlrd", reason="需要 xlrd 校验 .xls 有效性")
        book = xlrd.open_workbook(file_contents=minimal_xls_bytes())
        sheet = book.sheet_by_index(0)
        assert book.biff_version == 40                     # Excel 4.0 BIFF
        assert sheet.nrows >= 2
        assert sheet.cell_value(0, 0) == "手机号"
        assert sheet.cell_value(0, 1) == "金额"
        assert sheet.cell_value(1, 1) == 100.0

    def test_record_stream_starts_with_bof_and_ends_with_eof(self):
        data = minimal_xls_bytes()
        assert struct.unpack("<HH", data[:4]) == (0x0409, 8)   # BOF(BIFF4 工作表)
        assert struct.unpack("<HH", data[-4:]) == (0x000A, 0)  # EOF
        # CODEPAGE 必须在（否则按 ascii 解码中文会失败）
        assert struct.pack("<HH", 0x0042, 2) + struct.pack("<H", 936) in data

    def test_base64_round_trip(self):
        assert base64.b64decode(xls_base64()) == minimal_xls_bytes()

    def test_extension_differs_from_disallowed_sample(self):
        assert bad_extension_bytes().startswith(b"MZ")
        assert not minimal_xls_bytes().startswith(b"MZ")
