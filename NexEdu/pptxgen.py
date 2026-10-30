"""零依赖演示文稿（.pptx）生成器。

与 docxgen.py 同一思路：用标准库 zipfile 手写 OOXML 最小结构，不引入
python-pptx，保持「核心功能零第三方依赖」的项目约定。

结构：
  [Content_Types].xml
  _rels/.rels
  ppt/presentation.xml          幻灯片尺寸 + 页序
  ppt/_rels/presentation.xml.rels
  ppt/theme/theme1.xml          配色 / 字体（NexEdu 蓝）
  ppt/slideMasters/slideMaster1.xml
  ppt/slideLayouts/slideLayout1.xml
  ppt/slides/slideN.xml         正文页

Markdown → 幻灯片的分页规则（教学设计场景）：
- `#` 一级标题  → 章节封面页（大标题 + 副标题）
- `##` 二级标题 → 新的一页，标题为二级标题
- `###` 三级标题 → 本页内的小标题（作为一条要点）
- `-` 列表项    → 要点
- 其他段落      → 要点（超长自动截断）
- 表格          → 转为要点行（PPT 投影场景不渲染表格，避免字号过小）

分页约束：单页要点上限 _MAX_BULLETS 条，超出自动拆出「（续）」页；
总页数上限 _MAX_SLIDES，超出截断并在最后一页提示。

入口：
- pptx_bytes(title, markdown) -> bytes
- save_pptx(path, title, markdown) -> 字节数
- build_slides(title, markdown) -> [{"title","bullets","section"}]
"""

import io
import re
import zipfile

# ---------------- 版式常量（EMU：914400 = 1 英寸）----------------

_SLIDE_W = 12192000          # 16:9，13.333 英寸
_SLIDE_H = 6858000
_MARGIN_X = 685800
_TITLE_Y = 457200
_TITLE_H = 1219200
_BODY_Y = 1828800
_BODY_H = 4343400            # 1200 万 × 6858000 画布下的正文区

_ACCENT = "2F6FED"           # NexEdu 主色
_DARK = "1F2937"
_GREY = "6B7280"

_MAX_BULLETS = 6             # 单页要点上限（投影可读性）
_MAX_CHARS = 46              # 单条要点字数上限
_MAX_SLIDES = 40             # 总页数上限

_CITE_RE = re.compile(r"\[来源:[^\]]*\]")


def _esc(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def _clean(text):
    """清洗一条要点文本：去 Markdown 标记、引用标记、压缩空白、截断。"""
    t = _CITE_RE.sub("", text or "")
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)          # 去掉加粗标记
    t = re.sub(r"`(.+?)`", r"\1", t)
    t = t.replace("\\", "").strip()
    t = re.sub(r"\s+", " ", t)
    if len(t) > _MAX_CHARS:
        t = t[:_MAX_CHARS - 1] + "…"
    return t


def _is_table_sep(line):
    s = line.strip()
    return bool(re.match(r"^\s*\|?[\s:|-]+\|?\s*$", s)) and "-" in s


# ---------------- Markdown → 幻灯片大纲 ----------------

def build_slides(title, markdown):
    """把教案 Markdown 拆成幻灯片大纲。

    返回 [{"title": 页标题, "bullets": [...], "section": 是否章节封面}]。
    """
    md = markdown or ""
    slides = []
    if title:
        slides.append({"title": _clean(title)[:40], "bullets": [],
                       "section": True})

    cur = None

    def flush():
        """结束当前页。"""
        nonlocal cur
        if cur is not None:
            if cur["bullets"] or not cur["section"]:
                slides.append(cur)
            cur = None

    def new_slide(t, section=False):
        nonlocal cur
        flush()
        cur = {"title": _clean(t)[:40] or "要点", "bullets": [],
               "section": section}

    def add(text):
        """加一条要点；超上限自动续页。"""
        nonlocal cur
        if cur is None:
            new_slide("要点")
        t = _clean(text)
        if not t:
            return
        if len(cur["bullets"]) >= _MAX_BULLETS:
            # 避免连续续页出现「（续）（续）」：先剥掉已有后缀再统一加一次
            base = re.sub(r"（续）+$", "", cur["title"])
            new_slide(base + "（续）")
        cur["bullets"].append(t)

    lines = md.split("\n")
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        s = raw.strip()

        if not s:
            i += 1
            continue

        # 表格：投影不渲染，压缩成要点行，避免小字看不清
        if s.startswith("|") and s.count("|") >= 2 and not _is_table_sep(s):
            cells = [c.strip() for c in s.strip("|").split("|") if c.strip()]
            if cells and not _is_table_sep(s):
                add(" · ".join(cells))
            i += 1
            continue
        if _is_table_sep(s):
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            level, text = len(m.group(1)), m.group(2).strip()
            if level == 1:
                new_slide(text, section=True)
            elif level == 2:
                new_slide(text)
            else:
                add("【%s】" % text)      # 三级及以下 → 页内小标题
            i += 1
            continue

        m = re.match(r"^[-*]\s+(.*)$", s)
        if m:
            add(m.group(1).strip())
            i += 1
            continue

        m = re.match(r"^(\d+)[.、]\s*(.*)$", s)
        if m:
            add("%s. %s" % (m.group(1), m.group(2).strip()))
            i += 1
            continue

        add(s)
        i += 1

    flush()

    # 没有正文时至少给一页，避免生成空文件
    if not slides:
        slides.append({"title": _clean(title)[:40] or "演示文稿",
                       "bullets": ["（暂无内容）"], "section": False})

    if len(slides) > _MAX_SLIDES:
        slides = slides[:_MAX_SLIDES]
        slides[-1]["bullets"] = slides[-1]["bullets"][:_MAX_BULLETS - 1] + [
            "…… 后续内容见完整教案（Word）"]
    return slides


# ---------------- OOXML 零件 ----------------

def _content_types(n):
    over = "".join(
        '<Override PartName="/ppt/slides/slide%d.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.presentationml'
        '.slide+xml"/>' % i for i in range(1, n + 1))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types">'
        '<Default Extension="rels" ContentType="application/vnd.'
        'openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/ppt/presentation.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.presentationml'
        '.presentation.main+xml"/>'
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.'
        'presentationml.slideMaster+xml"/>'
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.'
        'presentationml.slideLayout+xml"/>'
        '<Override PartName="/ppt/theme/theme1.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.theme+xml"/>'
        + over + '</Types>'
    )


_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
    'officeDocument/2006/relationships/officeDocument" '
    'Target="ppt/presentation.xml"/></Relationships>'
)

_THEME = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'name="NexEdu"><a:themeElements>'
    '<a:clrScheme name="NexEdu">'
    '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
    '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="44546A"/></a:dk2>'
    '<a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
    '<a:accent1><a:srgbClr val="2F6FED"/></a:accent1>'
    '<a:accent2><a:srgbClr val="ED7D31"/></a:accent2>'
    '<a:accent3><a:srgbClr val="A5A5A5"/></a:accent3>'
    '<a:accent4><a:srgbClr val="FFC000"/></a:accent4>'
    '<a:accent5><a:srgbClr val="5B9BD5"/></a:accent5>'
    '<a:accent6><a:srgbClr val="70AD47"/></a:accent6>'
    '<a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
    '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
    '</a:clrScheme>'
    '<a:fontScheme name="NexEdu">'
    '<a:majorFont><a:latin typeface="Microsoft YaHei"/>'
    '<a:ea typeface="Microsoft YaHei"/><a:cs typeface="Microsoft YaHei"/>'
    '</a:majorFont>'
    '<a:minorFont><a:latin typeface="Microsoft YaHei"/>'
    '<a:ea typeface="Microsoft YaHei"/><a:cs typeface="Microsoft YaHei"/>'
    '</a:minorFont></a:fontScheme>'
    '<a:fmtScheme name="NexEdu">'
    '<a:fillStyleLst>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '</a:fillStyleLst>'
    '<a:lnStyleLst>'
    '<a:ln w="6350" cap="flat" cmpd="sng" algn="ctr">'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:prstDash val="solid"/></a:ln>'
    '<a:ln w="12700" cap="flat" cmpd="sng" algn="ctr">'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:prstDash val="solid"/></a:ln>'
    '<a:ln w="19050" cap="flat" cmpd="sng" algn="ctr">'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:prstDash val="solid"/></a:ln>'
    '</a:lnStyleLst>'
    '<a:effectStyleLst>'
    '<a:effectStyle><a:effectLst/></a:effectStyle>'
    '<a:effectStyle><a:effectLst/></a:effectStyle>'
    '<a:effectStyle><a:effectLst/></a:effectStyle>'
    '</a:effectStyleLst>'
    '<a:bgFillStyleLst>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '</a:bgFillStyleLst>'
    '</a:fmtScheme></a:themeElements></a:theme>'
)


def _presentation(n):
    # rId 必须与 _presentation_rels 一致：rId1 是母版，幻灯片从 rId2 起
    sld_ids = "".join(
        '<p:sldId id="%d" r:id="rId%d"/>' % (255 + i, i + 1)
        for i in range(1, n + 1))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:presentation xmlns:a="http://schemas.openxmlformats.org/'
        'drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships" xmlns:p="http://schemas.'
        'openxmlformats.org/presentationml/2006/main">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/>'
        '</p:sldMasterIdLst>'
        '<p:sldIdLst>%s</p:sldIdLst>'
        '<p:sldSz cx="%d" cy="%d"/>'
        '<p:notesSz cx="6858000" cy="9144000"/>'
        '</p:presentation>' % (sld_ids, _SLIDE_W, _SLIDE_H)
    )


def _presentation_rels(n):
    items = ['<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
             'officeDocument/2006/relationships/slideMaster" '
             'Target="slideMasters/slideMaster1.xml"/>']
    for i in range(1, n + 1):
        items.append(
            '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.'
            'org/officeDocument/2006/relationships/slide" '
            'Target="slides/slide%d.xml"/>' % (i + 1, i))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships">' + "".join(items) + '</Relationships>')


def _grp_sp_pr():
    return ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
            '</p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/>'
            '<a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
            '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')


_P_NS = (
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
)

_SLIDE_MASTER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<p:sldMaster %s>'
    '<p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg"/></p:bgRef></p:bg>'
    '<p:spTree>%s</p:spTree></p:cSld>'
    '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
    'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
    'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/>'
    '</p:sldLayoutIdLst></p:sldMaster>' % (_P_NS, _grp_sp_pr())
)

_SLIDE_MASTER_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
    'officeDocument/2006/relationships/slideLayout" '
    'Target="../slideLayouts/slideLayout1.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
    'officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
    '</Relationships>'
)

_SLIDE_LAYOUT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<p:sldLayout %s type="blank" preserve="1">'
    '<p:cSld name="Blank"><p:spTree>%s</p:spTree></p:cSld>'
    '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
    % (_P_NS, _grp_sp_pr())
)

_SLIDE_LAYOUT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
    'officeDocument/2006/relationships/slideMaster" '
    'Target="../slideMasters/slideMaster1.xml"/></Relationships>'
)

_SLIDE_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
    'officeDocument/2006/relationships/slideLayout" '
    'Target="../slideLayouts/slideLayout1.xml"/></Relationships>'
)


def _sp(shape_id, name, x, y, cx, cy, paragraphs, fill=None):
    """一个纯文本框（无填充无边框），paragraphs 为已渲染的 <a:p> 串。"""
    solid = ('<a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
             '<a:ln><a:noFill/></a:ln>' % fill) if fill else ''
    return (
        '<p:sp>'
        '<p:nvSpPr><p:cNvPr id="%d" name="%s"/>'
        '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>%s</p:spPr>'
        '<p:txBody><a:bodyPr wrap="square"><a:normAutofit/></a:bodyPr>'
        '<a:lstStyle/>%s</p:txBody></p:sp>'
    ) % (shape_id, _esc(name), x, y, cx, cy, solid, paragraphs)


def _para(text, size, bold=False, color=_DARK, space_before=0, indent=0):
    rpr = ('<a:rPr lang="zh-CN" altLang="en-US" sz="%d" b="%d" dirty="0">'
           '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
           '<a:latin typeface="Microsoft YaHei"/>'
           '<a:ea typeface="Microsoft YaHei"/></a:rPr>'
           % (size, 1 if bold else 0, color))
    return ('<a:p><a:pPr marL="%d" indent="-%d">'
            '<a:spcBef><a:spcPts val="%d"/></a:spcBef>'
            '<a:buChar char="•"/></a:pPr>'
            '<a:r>%s<a:t>%s</a:t></a:r></a:p>'
            % (indent + 285750, 285750, space_before, rpr, _esc(text)))


def _plain_para(text, size, bold=False, color=_DARK, algn="l"):
    rpr = ('<a:rPr lang="zh-CN" altLang="en-US" sz="%d" b="%d" dirty="0">'
           '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
           '<a:latin typeface="Microsoft YaHei"/>'
           '<a:ea typeface="Microsoft YaHei"/></a:rPr>'
           % (size, 1 if bold else 0, color))
    return ('<a:p><a:pPr algn="%s"/><a:r>%s<a:t>%s</a:t></a:r></a:p>'
            % (algn, rpr, _esc(text)))


def _slide_xml(slide):
    """渲染单页：标题 + 要点（章节页为居中大字 + 顶部色条）。"""
    title = slide.get("title") or ""
    bullets = slide.get("bullets") or []
    body = []

    if slide.get("section"):
        # 章节封面：左侧色条 + 居中大标题
        body.append(_sp(2, "Bar", 0, 2590800, 274320, 1676400,
                        _plain_para("", 1800), fill=_ACCENT))
        body.append(_sp(3, "SectionTitle", 1371600, 2590800, 9451800, 1676400,
                        _plain_para(title, 3600, bold=True, color=_ACCENT)))
    else:
        body.append(_sp(2, "Title", _MARGIN_X, _TITLE_Y,
                        _SLIDE_W - _MARGIN_X * 2, _TITLE_H,
                        _plain_para(title, 2800, bold=True, color=_ACCENT)))
        # 标题下分隔条
        body.append(_sp(3, "Line", _MARGIN_X, 1600200, 1600200, 38100,
                        _plain_para("", 800), fill=_ACCENT))
        paras = []
        if bullets:
            for b in bullets:
                paras.append(_para(b, 2000, space_before=800))
        else:
            paras.append(_para("（本页为章节分隔）", 1800, color=_GREY))
        body.append(_sp(4, "Content", _MARGIN_X, _BODY_Y,
                        _SLIDE_W - _MARGIN_X * 2, _BODY_H, "".join(paras)))

    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sld %s><p:cSld><p:spTree>%s%s</p:spTree></p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
            % (_P_NS, _grp_sp_pr(), "".join(body)))


# ---------------- 组装 ----------------

def pptx_bytes(title, markdown):
    """生成 .pptx 文件字节。"""
    slides = build_slides(title, markdown)
    n = len(slides)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types(n))
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("ppt/presentation.xml", _presentation(n))
        z.writestr("ppt/_rels/presentation.xml.rels", _presentation_rels(n))
        z.writestr("ppt/theme/theme1.xml", _THEME)
        z.writestr("ppt/slideMasters/slideMaster1.xml", _SLIDE_MASTER)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels",
                   _SLIDE_MASTER_RELS)
        z.writestr("ppt/slideLayouts/slideLayout1.xml", _SLIDE_LAYOUT)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels",
                   _SLIDE_LAYOUT_RELS)
        for i, s in enumerate(slides, 1):
            z.writestr("ppt/slides/slide%d.xml" % i, _slide_xml(s))
            z.writestr("ppt/slides/_rels/slide%d.xml.rels" % i, _SLIDE_RELS)
    return buf.getvalue()


def save_pptx(path, title, markdown):
    """落盘保存 .pptx，返回字节数。"""
    data = pptx_bytes(title, markdown)
    with open(path, "wb") as f:
        f.write(data)
    return len(data)
