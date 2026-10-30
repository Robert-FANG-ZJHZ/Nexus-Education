"""零依赖 Word（.docx）生成器。

用标准库 zipfile 手写 OOXML 最小结构（[Content_Types].xml / _rels/.rels /
word/document.xml），无需 python-docx。支持：
- 一级/二级/三级标题、正文、列表（-/条目与数字条目）
- **加粗** 行内格式
- Markdown 表格（| a | b |）→ Word 表格（含表头底色）
- 文档大标题（居中）

入口：
- docx_bytes(title, markdown) -> bytes   直接生成 Word 文件字节
- save_docx(path, title, markdown)       落盘保存
"""

import io
import re
import zipfile

# ---------------- XML 基础 ----------------

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType='
    '"application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType='
    '"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>'
)

_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type='
    '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    '</Relationships>'
)

_FONTS = ('<w:rFonts w:ascii="Calibri" w:eastAsia="Microsoft YaHei" '
          'w:hAnsi="Calibri" w:cs="Calibri"/>')

_SECT_PR = (
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1440" w:right="1360" w:bottom="1440" w:left="1360" '
    'w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>'
)


def _esc(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def _runs(text, bold=False, size=21, color=None):
    """把含 **加粗** 的文本转为一串 w:r（xml 字符串）。size 单位为半磅。"""
    parts = re.split(r"(\*\*.+?\*\*)", text)
    out = []
    for p in parts:
        if not p:
            continue
        b = bold
        if p.startswith("**") and p.endswith("**") and len(p) > 4:
            p = p[2:-2]
            b = True
        rpr = ('<w:rPr>' + _FONTS +
               ('<w:b/>' if b else '') +
               ('<w:color w:val="%s"/>' % color if color else '') +
               '<w:sz w:val="%d"/><w:szCs w:val="%d"/></w:rPr>') % (size, size)
        out.append('<w:r>' + rpr +
                    '<w:t xml:space="preserve">' + _esc(p) + '</w:t></w:r>')
    return "".join(out)


def _para(text, bold=False, size=21, color=None, before=80, after=80,
          center=False, indent=None):
    jc = '<w:jc w:val="center"/>' if center else ''
    ind = ('<w:ind w:left="%d" w:hanging="200"/>' % indent) if indent else ''
    return (
        '<w:p><w:pPr>'
        '<w:spacing w:before="%d" w:after="%d" w:line="312" w:lineRule="auto"/>'
        '%s%s</w:pPr>%s</w:p>'
    ) % (before, after, jc, ind, _runs(text, bold=bold, size=size, color=color))


def _cell(text, header=False, width=2400):
    shd = ('<w:tcPr><w:tcW w:w="%d" w:type="dxa"/>'
           '<w:shd w:val="clear" w:fill="EEF2F8"/></w:tcPr>'
           if header else
           '<w:tcPr><w:tcW w:w="%d" w:type="dxa"/></w:tcPr>' % width)
    runs = _runs(text, bold=header, size=19)
    return ('<w:tc>%s<w:p><w:pPr>'
            '<w:spacing w:before="40" w:after="40"/></w:pPr>%s</w:p></w:tc>'
            % (shd, runs))


def _table(rows):
    """rows: [[c1,c2,..], ...]，第一行为表头。"""
    borders = "".join(
        '<w:%s w:val="single" w:sz="4" w:space="0" w:color="C9CED6"/>' % side
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    ncols = max(len(r) for r in rows)
    width = int(8600 / max(ncols, 1))
    grid = "".join('<w:gridCol w:w="%d"/>' % width for _ in range(ncols))
    trs = []
    for i, row in enumerate(rows):
        row = list(row) + [""] * (ncols - len(row))  # 补齐短行
        tcs = "".join(_cell(c, header=(i == 0), width=width) for c in row)
        trs.append("<w:tr>%s</w:tr>" % tcs)
    return ('<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/>'
            '<w:tblBorders>%s</w:tblBorders>'
            '<w:tblCellMar><w:left w:w="80" w:type="dxa"/>'
            '<w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr>'
            '<w:tblGrid>%s</w:tblGrid>%s</w:tbl>'
            % (borders, grid, "".join(trs)))


def _split_table_line(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_table_sep(line):
    return bool(re.match(r"^\s*\|?[\s:|-]+\|?\s*$", line)) and "-" in line


def _is_table_line(line):
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


# ---------------- Markdown → document.xml ----------------

def _md_to_body(md):
    out = []
    lines = md.split("\n")
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        t = raw.rstrip()
        stripped = t.strip()

        # 表格块
        if _is_table_line(t) and i + 1 < n and _is_table_sep(lines[i + 1]):
            rows = [_split_table_line(t)]
            i += 2
            while i < n and _is_table_line(lines[i]):
                rows.append(_split_table_line(lines[i]))
                i += 1
            out.append(_table(rows))
            continue

        if not stripped:
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level, text = len(m.group(1)), m.group(2).strip()
            if level == 1:
                out.append(_para(text, bold=True, size=30, color="2F6FED",
                                 before=240, after=120))
            elif level == 2:
                out.append(_para(text, bold=True, size=26,
                                 before=220, after=100))
            else:
                out.append(_para(text, bold=True, size=22, color="333333",
                                 before=160, after=80))
            i += 1
            continue

        m = re.match(r"^[-*]\s+(.*)$", stripped)
        if m:
            out.append(_para("• " + m.group(1).strip(), size=21,
                             indent=200, before=40, after=40))
            i += 1
            continue

        m = re.match(r"^(\d+)[.、]\s*(.*)$", stripped)
        if m:
            out.append(_para(stripped, size=21, indent=200,
                             before=40, after=40))
            i += 1
            continue

        # 普通段落
        out.append(_para(stripped))
        i += 1
    return "".join(out)


def docx_bytes(title, markdown, meta_lines=None):
    """生成 .docx 文件字节。title 作为文档大标题（居中）。

    meta_lines: 标题下的元信息行（如 学科/学段/生成时间）。
    """
    body = []
    if title:
        body.append(_para(title, bold=True, size=40, before=0, after=120,
                          center=True))
    for m in (meta_lines or []):
        if m:
            body.append(_para(m, size=17, color="8F8F8F", after=40,
                              center=True))
    if meta_lines:
        body.append('<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" '
                    'w:space="4" w:color="E4E7EC"/></w:pBdr></w:pPr></w:p>')
    body.append(_md_to_body(markdown or ""))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body>'
        + "".join(body) + _SECT_PR + "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


def save_docx(path, title, markdown, meta_lines=None):
    """落盘保存 .docx，返回字节数。"""
    data = docx_bytes(title, markdown, meta_lines)
    with open(path, "wb") as f:
        f.write(data)
    return len(data)
