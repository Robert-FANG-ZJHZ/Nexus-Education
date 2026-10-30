"""本地知识库：文档自动切片 + 关键词检索。

数据源（启动时自动构建，进入 Agent 即完成切片）：
1. 桌面「知识库-优秀教学案例」目录（.txt / .docx / .md）
2. 桌面「知识库-教科书」目录（.pdf，pypdf 提取）
3. workspace/knowledge 目录（用户自放文档）

处理流程：
- 首次启动：扫描全部源文件 → 提取文本 → 清洗 → 切片（约 600 字，重叠 80）
  → 缓存到 workspace/kb/chunks.json
- 再次启动：按文件指纹（mtime+size）增量更新，未变化的文件直接复用缓存
- 检索：所有切片拼接为单一长文本，关键词用 str.find 定位 + 偏移二分映射回切片，
  纯 C 实现扫描，百万级文本毫秒完成

保留原有内置教学理论条目（课标/量规/UbD 等），与本地切片一起参与检索。
"""

import bisect
import hashlib
import json
import math
import os
import re
import threading
import time
import zipfile
import xml.etree.ElementTree as ET

from . import config

# ---------------- 源目录与参数 ----------------

def _default_source_dirs():
    """运行时扫描的源目录。

    注意：**随包分发的内置知识库不走这里**。它由构建期的
    tools/build_builtin_kb.py 切片定型后加密嵌入 exe，运行时原样加载，
    因此桌面的预置教科书目录不再参与运行时扫描（换机器没有它也不影响使用）。

    这里只负责教师自己放入的文档，以及用 NEXEDU_KB_DIRS 额外指定的目录。
    """
    dirs = [os.path.join(config.WORKSPACE_DIR, "knowledge")]
    # 允许用环境变量追加（多个目录用 os.pathsep 分隔），便于换机器不改代码
    for p in (os.environ.get("NEXEDU_KB_DIRS") or "").split(os.pathsep):
        p = p.strip()
        if p and os.path.isdir(p) and p not in dirs:
            dirs.append(p)
    return dirs

SOURCE_DIRS = _default_source_dirs()
SUPPORTED_EXT = (".txt", ".md", ".docx", ".doc", ".pptx", ".pdf",
                 ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff",
                 ".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg", ".flac")
# 需要识别才能取到文字的类型（无文本层）
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")
_AUDIO_EXT = (".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg", ".flac")
# 老二进制格式：无第三方依赖时无法解析，必须明确告知而不是静默丢弃
_LEGACY_EXT = (".doc",)

CHUNK_SIZE = 600     # 每片目标字数
CHUNK_OVERLAP = 80   # 相邻片重叠字数
MIN_CHUNK = 60       # 过短丢弃

KB_DIR = os.path.join(config.WORKSPACE_DIR, "kb")
CACHE_PATH = os.path.join(KB_DIR, "chunks.json")

# ---------------- 内置教学理论条目（与本地切片共同检索） ----------------

KNOWLEDGE = [
    {
        "cat": "专家矩阵",
        "tags": ["专家矩阵", "NexAim", "NexDes", "NexSca", "工作流", "闭环", "山海智绘"],
        "text": "山海智绘·Nexus 专家矩阵（V1-OpenFree）：NexAim（目标设计专家）→ NexDes（教学设计专家）→ NexSca（作业与评价专家）构成「教-学-评」一体化闭环。NexAim 上游对接国家课标、教材与区域政策，输出《课标转化卡》《课时目标叙写》《目标-评价对应表》等目标体系；NexDes 承接目标体系，评价任务前置，按「目标—评价—活动」逆向输出《课时教学设计》《评价任务单》《课堂观察记录表》《学习活动单》；NexSca 承接目标与评价任务，输出《目标-题目对应表》《分层作业单》《评价量规》《错因诊断报告》《补偿练习包》，并回流作业结果与错因分布，用于优化目标分解与评价设计。全流程坚持人机协同：AI 只提供建议与依据，教师拥有最终裁定权。",
    },
    {
        "cat": "课标",
        "tags": ["语文", "阅读", "目标", "课标", "核心素养"],
        "text": "《义务教育语文课程标准（2022年版）》强调「语言建构与运用、思维发展与提升、审美鉴赏与创造、文化传承与理解」四大核心素养，主张以学习任务群组织课程内容，倡导「教-学-评」一体化。",
    },
    {
        "cat": "课标",
        "tags": ["数学", "目标", "课标", "核心素养", "运算"],
        "text": "《义务教育数学课程标准（2022年版）》以「会用数学的眼光观察、用数学的思维思考、用数学的语言表达」为核心素养，强调运算能力、推理意识、数据意识等学段目标的分层表述。",
    },
    {
        "cat": "课标",
        "tags": ["科学", "目标", "课标", "探究", "素养"],
        "text": "《义务教育科学课程标准（2022年版）》强调科学观念、科学思维、探究实践、态度责任四类核心素养，倡导基于真实情境的探究式学习与过程性评价。",
    },
    {
        "cat": "设计理论",
        "tags": ["逆向设计", "目标", "评价", "UbD", "教案"],
        "text": "逆向教学设计（UbD）主张「目标—评价—活动」三步逆向：先明确预期学习结果，再确定可接受的评价证据，最后设计学习体验与教学活动，使评价前置到教学设计环节。",
    },
    {
        "cat": "评价量规",
        "tags": ["量规", "评价", "表现性评价", "作业", "标准"],
        "text": "评价量规（Rubric）应包含评价维度、表现等级与描述性指标，覆盖知识与能力、过程与方法；分层可设为「基础/进阶/挑战」或「水平1~4」，避免用单一分数压缩复杂素养。",
    },
    {
        "cat": "错因诊断",
        "tags": ["错因", "作业", "诊断", "补偿", "知识追踪"],
        "text": "常见错因类型：概念混淆、审题不清、运算/书写失误、方法迁移不足、表达不规范。可结合聚类、关联规则与知识追踪定位薄弱知识点，生成变式题与微专题补救。",
    },
    {
        "cat": "作业设计",
        "tags": ["分层作业", "作业", "基础", "创新", "负担"],
        "text": "分层作业通常划分为「基础层（识记理解）/ 应用层（迁移运用）/ 创新层（综合评价与创造）」，并依据作业时长与题量进行总量与难度监测，落实「双减」的负担预警要求。",
    },
]

# ---------------- 运行时状态 ----------------

_STATE = {
    "phase": "idle",          # idle / building / ready / error
    "total_files": 0,
    "done_files": 0,
    "total_chunks": 0,
    "seed_chunks": 0,         # 内嵌种子切片数
    "local_chunks": 0,        # 用户本地切片数
    "current": "",
    "message": "",
    "failed": [],         # 未提取到内容的文件 [{file, error}]
}

# 检索结构（构建完成后原子替换）
_chunks = []        # [{"source":..., "text":...}, ...]
_ALL_TEXT = ""
_OFFSETS = []
_LOCK = threading.RLock()   # 可重入：_rebuild 可能被持锁的调用方再次进入

# BM25 统计（随索引重建一并刷新）
_STATS = {"n": 0, "avgdl": 1.0}
_DF_CACHE = {}              # 词 → 在全文中的出现次数（近似 df，进程内缓存）
_K1 = 1.5
_B = 0.75

# 切片 ID → 切片（供引用可追溯按 ID 取回原文，避免整库暴露）
_CHUNK_BY_ID = {}


def status():
    return dict(_STATE)


# ---------------- 文本提取 ----------------

def _normalize_text(t):
    """清洗提取文本：合并断词连字符、去除中文间换行、压缩空白。"""
    if not t:
        return ""
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"-\n", "", t)                                # 英文断词
    t = re.sub(r"(?<=[\u4e00-\u9fff，。；：、！？“”»》）】])\n(?=[\u4e00-\u9fff“”«《（【])", "", t)
    t = re.sub(r"(?<=[\u4e00-\u9fff])\n(?=[\u4e00-\u9fff])", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


def _read_txt(path):
    for enc in ("utf-8", "gb18030", "utf-16"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""


def _read_docx(path):
    """零依赖解析 docx：zipfile + XML 提取 w:t 文本。"""
    try:
        with zipfile.ZipFile(path) as z:
            xml_bytes = z.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        parts = []
        for para in root.iter(ns + "p"):
            line = "".join(t.text or "" for t in para.iter(ns + "t"))
            if line.strip():
                parts.append(line)
        return "\n".join(parts)
    except (OSError, ET.ParseError, KeyError, zipfile.BadZipFile):
        return ""


def _read_pdf(path):
    """pypdf 逐页提取；扫描件（无文本层）回退到本地 OCR（若可用）。

    扫描版教科书此前会静默变成空文档——教师以为加进了知识库，实际什么都没有。
    现在要么拿到文字，要么给出明确原因。
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        _STATE["message"] = "未安装 pypdf，PDF 教科书已跳过"
        return ""
    try:
        reader = PdfReader(path)
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n".join(parts)
    except Exception:
        return ""

    if len(text.strip()) >= 50:
        return text

    # 文本层为空 → 多半是扫描件，尝试本地 OCR
    try:
        from . import ocr
        ocr_text, why = ocr.try_extract(path)
        if ocr_text:
            _STATE["message"] = f"{os.path.basename(path)}：{why}"
            return ocr_text
        _STATE["message"] = (f"{os.path.basename(path)} 疑似扫描版，未能提取文字"
                             f"（{why or '无文本层'}）")
    except Exception:
        pass
    return text


def _read_pptx(path):
    """提取 PowerPoint 文本（零依赖：pptx 是 zip + OOXML，取 <a:t> 文字节点）。

    按 slide 顺序读取并标注页码，这样引用时能说清"第几页"，
    教师核对方便；板书/课件类资料主要价值就在文字与顺序。
    """
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist()
                     if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
            def _page(n):
                m = re.search(r"slide(\d+)\.xml", n)
                return int(m.group(1)) if m else 0
            names.sort(key=_page)
            parts = []
            for i, n in enumerate(names, 1):
                try:
                    xml = z.read(n).decode("utf-8", "ignore")
                except Exception:
                    continue
                texts = re.findall(r"<a:t>(.*?)</a:t>", xml, re.S)
                line = " ".join(t.strip() for t in texts if t.strip())
                if line:
                    parts.append(f"[第{i}页] {line}")
            return "\n".join(parts)
    except Exception:
        return ""


def _extract(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _read_pdf(path)
    if ext == ".docx":
        return _read_docx(path)
    if ext == ".pptx":
        return _read_pptx(path)
    if ext in _LEGACY_EXT:
        # .doc 是 OLE2 二进制格式，无第三方依赖解析不了。
        # 明确告知，而不是让教师以为加进了知识库、结果什么都没进去。
        _STATE["message"] = (f"{os.path.basename(path)} 是旧版 .doc 二进制格式，"
                             f"无法直接解析；请用 Word 另存为 .docx 后重新上传")
        return ""
    if ext in _IMAGE_EXT:
        # 图片无文本层：走本地 OCR；引擎缺失时 ocr 会给出明确原因，不静默失败
        text = ""
        try:
            from . import ocr
            text, why = ocr.try_extract(path)
            if not text:
                _STATE["message"] = f"{os.path.basename(path)}：{why}"
        except Exception:
            pass
        # 若教师开启了图片理解，再补一段场景描述（板书 / 作业 / 试卷）。
        # OCR 只能给出"写了什么"，图像理解能给出"这是什么"——两者互补，
        # 都写入同一切片，检索时任一命中都能召回。
        try:
            from . import vision
            if vision.available():
                r = vision.describe(
                    path,
                    "请客观描述这张图片：若是板书请说明板书结构与要点；"
                    "若是学生作业请说明题型与作答情况；若含文字请一并识别。")
                if r.get("text"):
                    text = (text + "\n[图像理解] " + r["text"]).strip()
                    _STATE["message"] = (f"{os.path.basename(path)}："
                                         f"已 OCR 并补充图像理解")
        except Exception:
            pass
        return text
    if ext in _AUDIO_EXT:
        try:
            from . import speech
            r = speech.transcribe(path)
            if r.get("text"):
                return r["text"]
            _STATE["message"] = f"{os.path.basename(path)}：{r.get('error')}"
        except Exception:
            pass
        return ""
    return _read_txt(path)


# ---------------- 切片 ----------------

def _make_chunks(text, source, meta=None):
    """切片。meta 为该来源的结构化标签（学科/年级/版本…），随每片一起存储。"""
    text = _normalize_text(text)
    if not text:
        return []
    meta = dict(meta or {})
    chunks = []
    step = max(CHUNK_SIZE - CHUNK_OVERLAP, 1)
    n = len(text)
    i = 0
    while i < n:
        piece = text[i:i + CHUNK_SIZE].strip()
        if len(piece) >= MIN_CHUNK:
            chunks.append({"source": source, "text": piece, "meta": meta})
        if i + CHUNK_SIZE >= n:
            break
        i += step
    if not chunks and text:
        chunks.append({"source": source, "text": text[:CHUNK_SIZE * 2],
                       "meta": meta})
    return chunks


# ---------------- 构建 ----------------

def _scan_source_files():
    """递归扫描源目录。

    支持子目录，方便按「教科书 / 课程标准 / 优秀教学案例」分类存放；
    隐藏目录与 Office 临时文件（~$）跳过。
    """
    files, seen = [], set()
    for d in SOURCE_DIRS:
        if not os.path.isdir(d):
            continue
        for root, subdirs, fns in os.walk(d):
            subdirs[:] = [x for x in subdirs
                          if not x.startswith(".") and not x.startswith("~")]
            for fn in sorted(fns):
                if fn.startswith("~$") or not fn.lower().endswith(SUPPORTED_EXT):
                    continue
                path = os.path.join(root, fn)
                if os.path.isfile(path) and path not in seen:
                    seen.add(path)
                    files.append(path)
    return files


def _fingerprint(path):
    try:
        st = os.stat(path)
        return {"mtime": int(st.st_mtime), "size": st.st_size}
    except OSError:
        return None


# 断点续跑用的半成品缓存：单独一份文件。
# 真实教训：曾把 partial 直接写进主缓存文件，构建过半时主缓存被半份数据覆盖，
# 一旦中途退出，下次启动就拿半份切片建索引，知识库「少了一半」还查不出原因。
PARTIAL_PATH = CACHE_PATH + ".partial"


def _load_cache():
    for path in (CACHE_PATH, PARTIAL_PATH):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
    return {"files": {}}


def _save_cache(cache):
    os.makedirs(KB_DIR, exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)
    try:                      # 主缓存落定，半成品即可丢弃
        if os.path.exists(PARTIAL_PATH):
            os.remove(PARTIAL_PATH)
    except OSError:
        pass


def _save_partial(cache):
    """写半成品缓存（断点续跑用），绝不覆盖主缓存。"""
    os.makedirs(KB_DIR, exist_ok=True)
    tmp = PARTIAL_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(cache, partial=True), f, ensure_ascii=False)
        os.replace(tmp, PARTIAL_PATH)
    except OSError:
        pass


# ---------------- 内嵌种子知识库（随 exe 分发，只读） ----------------

_SEED = None          # 内嵌种子切片缓存


_BUILTIN_PAK = "kb/builtin_kb.json"   # 构建期切片定型的内置知识库（加密随包）


def _load_seed():
    """读取**内置知识库**切片（首次读取后常驻内存）。

    内置知识库不是运行时缓存：它由构建期 tools/build_builtin_kb.py 一次性切片
    定型并加密嵌入 exe，运行时直接解密加载，不依赖桌面源目录、也不参与增量比对。
    打包环境从 assets.pak 解密；源码环境退回读 nexedu/data/builtin_kb.json。
    """
    global _SEED
    if _SEED is not None:
        return _SEED
    _SEED = []
    data = None
    try:
        from . import assets
        raw = assets.read_data(_BUILTIN_PAK)
        if raw:
            data = json.loads(raw.decode("utf-8"))
    except Exception:
        data = None
    if data is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "builtin_kb.json")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                data = None
    if data:
        _SEED = list(data.get("chunks") or [])
    return _SEED


def _cid(source, text):
    """切片稳定 ID：由来源与正文摘要派生，供引用可追溯使用。

    用内容派生而非下标，索引重建后同一片仍指向同一 ID，教师的引用链接不会失效。
    """
    raw = "%s|%s" % (source or "", (text or "")[:160])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


# 轻量向量索引（与 _chunks 一一对应，随主索引重建）
_VECTORS = []
_VECTOR_IDS = []
_VEC_WEIGHT = 0.45       # 向量分在混合分中的权重（BM25 占其余）
# 单次检索内的向量分缓存（用完即清）。**必须按线程隔离**：并行任务下若共用
# 一个模块级字典，甲线程刚写入就被乙线程 clear()，甲再按切片下标取分就会
# KeyError（曾在线上偶发为「节点执行失败：1028」）。
_VEC_TLS = threading.local()


def _vec_score():
    """当前线程独占的向量分缓存（每次检索开始时清空）。"""
    d = getattr(_VEC_TLS, "score", None)
    if d is None:
        d = {}
        _VEC_TLS.score = d
    return d


def _rebuild_index(chunks):
    """重建检索结构（拼接长文本 + 偏移表 + BM25 统计 + 切片 ID 表 + 向量索引）。"""
    global _chunks, _ALL_TEXT, _OFFSETS
    offsets, buf, pos = [], [], 0
    total_len = 0
    _CHUNK_BY_ID.clear()
    # 内置理论条目在模块加载时注册过 ID，clear 会让它们随重建一起丢失，
    # 结果是「理论条目被引用后，服务端按 ID 核对恒为 404」。此处重新注册。
    for _item in KNOWLEDGE:
        _CHUNK_BY_ID[_item["id"]] = {
            "source": "【%s】" % (_item.get("cat") or "理论"),
            "text": _item.get("text") or ""}
    for c in chunks:
        c["id"] = _cid(c.get("source", ""), c.get("text", ""))
        _CHUNK_BY_ID[c["id"]] = c
        offsets.append(pos)
        buf.append(c["text"])
        total_len += len(c["text"])
        pos += len(c["text"]) + 1   # +1 分隔符，避免跨片误匹配
    text = "\n".join(buf)
    # 轻量向量索引随主索引一起重建（关闭时跳过，不影响 BM25）
    vecs, ids = [], []
    try:
        from . import vectors
        if vectors.enabled():
            vectors.rebuild(chunks)              # 单一索引，避免两份重复计算
            vecs = vectors.all_vectors()
            ids = vectors.all_ids()
    except Exception:
        pass

    # 先算完再一次性替换：真实教训是「先赋 _chunks、后建向量索引」，
    # 并行查询会在向量索引还是空的时候读到新切片表，检索结果忽有忽无。
    # 这里把全部结构准备好后统一切换，查询线程只会看到「旧索引」或「新索引」。
    _chunks = chunks
    _ALL_TEXT = text
    _OFFSETS = offsets
    _DF_CACHE.clear()
    _STATS["n"] = len(chunks)
    _STATS["avgdl"] = (total_len / len(chunks)) if chunks else 1.0
    _VECTORS[:] = vecs
    _VECTOR_IDS[:] = ids


# ---------------- BM25 打分（纯标准库实现） ----------------

def _df(term):
    """词的文档频率近似值（在拼接全文中的出现次数），带缓存。

    说明：严格 df 应统计「出现过该词的切片数」，代价是要维护倒排索引。
    这里用全文出现次数近似——区分度趋势一致，且可用 str.count 的 C 级速度，
    在百万级文本上单次查询仍是毫秒级，无需引入任何第三方依赖。
    """
    v = _DF_CACHE.get(term)
    if v is None:
        v = _ALL_TEXT.count(term) if _ALL_TEXT else 0
        _DF_CACHE[term] = v
    return v


def _idf(term):
    n = max(_STATS["n"], 1)
    df = _df(term)
    # 平滑 IDF：df 越大（越常见）值越小，df=0 时不至于除零
    return math.log(1.0 + (n - df + 0.5) / (df + 0.5))


def _bm25(tf, dl, idf):
    """BM25 单项得分：IDF × 词频饱和 × 长度归一化。"""
    denom = tf + _K1 * (1 - _B + _B * dl / (_STATS["avgdl"] or 1.0))
    return idf * (tf * (_K1 + 1) / denom) if denom > 0 else 0.0


_SUBJECT_TOKENS = ("语文", "数学", "英语", "科学", "历史", "地理",
                   "道法", "道德", "生物", "物理", "化学")
_GRADE_TOKENS = ("七年级", "八年级", "九年级", "初一", "初二", "初三",
                 "七上", "七下", "八上", "八下", "九上", "九下")

_VERSION_TOKENS = ("人教版", "部编版", "人教", "外研版", "浙教版",
                   "苏教版", "北师大版", "沪教版")
_CATEGORY_RULES = (
    ("知识库-教科书", "教科书"),
    ("知识库-课程标准", "课程标准"),
    ("知识库-优秀教学案例", "教学案例"),
    ("教科书", "教科书"),
    ("课程标准", "课程标准"),
    ("教学案例", "教学案例"),
)


def _parse_meta(path):
    """从路径与文件名解析结构化标签：类别 / 学科 / 年级 / 版本 / 册次。

    教科书文件名形如「最新【人教版】8年级语文课本•上册.pdf」，
    这类元信息抽成标签后，检索可直接按学科、学段加权与过滤，
    不必再靠来源字符串做模糊匹配。
    """
    name = os.path.basename(path)
    full = (path or "").replace("\\", "/")
    meta = {}

    for key, val in _CATEGORY_RULES:
        if key in full:
            meta["category"] = val
            break

    for t in _SUBJECT_TOKENS:
        if t in name:
            meta["subject"] = "道德与法治" if t in ("道法", "道德") else t
            break

    # 注意：文件名既有「8年级」也有「八年级」，两种写法都要认
    m = re.search(r"([七八九7-9])年级", name)
    if m:
        num = m.group(1)
        meta["grade"] = {"7": "七", "8": "八", "9": "九"}.get(num, num) + "年级"
    else:
        m2 = re.search(r"(七年级|八年级|九年级|初一|初二|初三)", name)
        if m2:
            meta["grade"] = m2.group(1)

    for t in _VERSION_TOKENS:
        if t in name:
            meta["version"] = t
            break

    if "上册" in name:
        meta["volume"] = "上册"
    elif "下册" in name:
        meta["volume"] = "下册"

    return meta


# 内置理论条目也赋予稳定 ID，与切片 ID 一同进入引用池
for _i, _item in enumerate(KNOWLEDGE):
    _item["id"] = _cid("理论·" + (_item.get("cat") or ""),
                       (_item.get("text") or "")[:160])
    _CHUNK_BY_ID[_item["id"]] = {"source": "【%s】" % _item.get("cat", "理论"),
                                 "text": _item.get("text", "")}


def _source_bonus(source, query):
    """来源名命中同学科/学段词时给予加权（如查询含「语文」且来源含「语文」）。"""
    s = source or ""
    if not s:
        return False
    for t in _SUBJECT_TOKENS:
        if t in query and t in s:
            return True
    for t in _GRADE_TOKENS:
        if t in query and t in s:
            return True
    return False


def _rebuild():
    """种子 + 本地缓存合并（按来源文件名去重，种子优先）并重建索引。"""
    with _LOCK:
        seed = _load_seed()
        seed_names = {c["source"] for c in seed}
        chunks = list(seed)
        cache = _load_cache()
        for path, entry in (cache.get("files") or {}).items():
            if os.path.basename(path) in seed_names:
                continue
            chunks.extend(entry.get("chunks") or [])
        _rebuild_index(chunks)
        _STATE["total_chunks"] = len(chunks)
        _STATE["seed_chunks"] = len(seed)
        _STATE["local_chunks"] = len(chunks) - len(seed)
        try:                                   # 知识结构索引（学科/学段/知识点）
            from . import kg
            kg.build(chunks)
        except Exception:
            pass


def build_all():
    """扫描源目录并完成切片（增量）。供启动时后台线程调用。"""
    with _LOCK:
        if _STATE["phase"] == "building":
            return
        _STATE["phase"] = "building"
        _STATE["message"] = ""

    try:
        files = _scan_source_files()
        cache = _load_cache()
        old_files = cache["files"]

        # 内嵌种子已有的同名文档直接跳过（不再重新提取）
        seed_names = {c["source"] for c in _load_seed()}

        _STATE["total_files"] = len(files)
        _STATE["done_files"] = 0
        _STATE["failed"] = []

        # 安全闸：源文件一个都没扫到时**绝不覆盖已有缓存**。
        # 真实教训：桌面上的源目录被移动/改名后，扫描结果为空，
        # 一次例行构建就把几 MB 的既有切片全写没了——丢数据比建库失败严重得多。
        # 因此宁可保留旧数据并明确告警，也不写空。
        # 但**内置知识库是随包分发的**：教师还没自己上传文档时 files=0 属于
        # 正常状态，不该报"源目录被移动"。只有连内置知识库都没有时才告警。
        if not files and not _load_seed() \
                and any((v.get("chunks") or []) for v in old_files.values()):
            _STATE["phase"] = "ready"
            _STATE["message"] = (
                "未扫描到任何源文件（源目录可能已被移动或改名），"
                "已保留原有知识库内容，未做任何覆盖。")
            _rebuild()
            return

        new_cache = {"built_at": time.time(), "files": {}}

        for path in files:
            name = os.path.basename(path)
            _STATE["current"] = name
            if name in seed_names:
                # 种子已包含同名文档：复用内嵌版本
                _STATE["done_files"] += 1
                continue
            fp = _fingerprint(path)
            if fp is None:
                continue
            entry = old_files.get(path)
            # 预置目录（随包分发的教科书等）里个别文件提取失败不打扰教师，
            # 只有教师自己导入的文件失败才进提示列表。
            mine = _is_user_source(path)
            if entry and entry.get("fp") == fp:
                # 源未变化：复用缓存切片
                chunks = entry.get("chunks", [])
                if not chunks and mine:
                    # 上次就未提取到内容：仍列入提示，便于用户重新导出源文件
                    _STATE["failed"].append(
                        {"file": name, "error": "未提取到内容（编码损坏或扫描版）"})
            else:
                try:
                    text = _extract(path)
                    chunks = _make_chunks(text, name, _parse_meta(path))
                except Exception as e:
                    # 单文件失败不中断整体构建，记录并继续
                    if mine:
                        _STATE["failed"].append({"file": name, "error": str(e)})
                    chunks = []
                if not chunks and fp["size"] > 1024 and mine:
                    # 提取为空（内容损坏 / 扫描版 PDF 等），记录便于排查
                    _STATE["failed"].append({"file": name, "error": "未提取到文本（文件内容损坏或为扫描版）"})
            new_cache["files"][path] = {"fp": fp, "chunks": chunks}
            _STATE["done_files"] += 1
            # 每 10 个文件落盘一次：兼顾断点续跑与 IO 开销（缓存可达数 MB）
            if _STATE["done_files"] % 10 == 0:
                _save_partial(new_cache)

        _save_cache(new_cache)
        _rebuild()

        _STATE["phase"] = "ready"
        _STATE["current"] = ""
        failed = _STATE.get("failed") or []
        if failed:
            _STATE["message"] = (f"已索引 {_STATE['total_chunks']} 片"
                                 f" · {len(failed)} 个文件未提取到内容")
        else:
            _STATE["message"] = f"已索引 {_STATE['total_chunks']} 片"
    except Exception as e:
        _STATE["phase"] = "error"
        _STATE["message"] = f"知识库构建失败：{e}"


# ---------------- 用户本地知识库管理（上传 / 删除 / 列表） ----------------

def _user_dir():
    return os.path.join(config.WORKSPACE_DIR, "knowledge")


def _is_user_source(path):
    """是否为教师自行导入的来源（而非随包分发的预置目录）。

    预置目录（桌面「山海智绘·知识库建设」等）里个别文件提取失败属于
    **开发者侧的数据问题**，不进失败列表、不打扰教师；
    只有教师自己上传 / 导入的文件失败才提示。
    """
    try:
        ud = os.path.abspath(_user_dir())
        ap = os.path.abspath(path)
        return ap == ud or ap.startswith(ud + os.sep)
    except Exception:
        return False


def _refresh_message():
    n = _STATE.get("total_chunks", 0)
    failed = _STATE.get("failed") or []
    _STATE["message"] = (f"已索引 {n} 片 · {len(failed)} 个文件未提取到内容"
                         if failed else f"已索引 {n} 片")


def dismiss_failed(name=None):
    """手动清除导入失败提示；name 为空则清空全部。

    教师确认过、或不想再看到某条提示时可手动删除。
    注意：下次构建若该文件仍然失败，提示会重新出现（不会被永久屏蔽）。
    """
    with _LOCK:
        cur = list(_STATE.get("failed") or [])
        if not name:
            _STATE["failed"] = []
        else:
            _STATE["failed"] = [f for f in cur if f.get("file") != name]
        removed = len(cur) - len(_STATE["failed"])
        _refresh_message()
    return removed


def list_local():
    """返回用户本地添加的文档列表（名称 / 片数 / 大小）。"""
    d = _user_dir()
    cache = _load_cache()
    out = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            if not os.path.isfile(p) or not fn.lower().endswith(SUPPORTED_EXT):
                continue
            if fn.startswith("~$"):
                continue
            entry = (cache.get("files") or {}).get(p)
            n = len(entry.get("chunks") or []) if entry else 0
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            out.append({"name": fn, "chunks": n, "size": size})
    return out


def add_file(path):
    """处理单个用户文档：提取 → 切片 → 写入本地缓存 → 重建索引。
    返回切片数（0 表示未提取到内容）。"""
    name = os.path.basename(path)
    with _LOCK:
        try:
            text = _extract(path)
            chunks = _make_chunks(text, name)
        except Exception:
            chunks = []
        cache = _load_cache()
        cache.setdefault("files", {})[path] = {"fp": _fingerprint(path), "chunks": chunks}
        _save_cache(cache)
    _rebuild()
    if _STATE["phase"] != "ready":
        _STATE["phase"] = "ready"
    _STATE["message"] = f"已索引 {_STATE['total_chunks']} 片"
    return len(chunks)


def remove_doc(name):
    """删除用户本地文档（原始文件 + 切片缓存）并重建索引。"""
    name = os.path.basename(name)   # 防路径穿越
    path = os.path.join(_user_dir(), name)
    with _LOCK:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        cache = _load_cache()
        (cache.get("files") or {}).pop(path, None)
        _save_cache(cache)
    _rebuild()
    _STATE["message"] = f"已索引 {_STATE['total_chunks']} 片"


_WARM_STARTED = False
_WARM_DONE = threading.Event()


def warm_async():
    """后台预热索引（种子 + 本地缓存），让第一个提问不用等索引重建。

    真实教训：过去 _ensure_loaded 在**请求线程**里持锁做全量 _rebuild()，
    而启动时 build_all 正握着同一把锁扫盘；两条线程互相等，
    表现就是「点下第一个问题后界面长时间不动」。预热挪到后台后，
    请求路径上只剩一次锁内二次判断，毫秒级。
    """
    global _WARM_STARTED
    if _WARM_STARTED:
        return
    _WARM_STARTED = True

    def _run():
        try:
            with _LOCK:
                if not _chunks:
                    _rebuild()
                if _STATE["phase"] != "building":
                    _STATE["phase"] = "ready"
                    _STATE["message"] = f"已索引 {_STATE['total_chunks']} 片"
        except Exception:
            pass
        finally:
            _WARM_DONE.set()

    threading.Thread(target=_run, daemon=True).start()


def _ensure_loaded(wait=5.0):
    """查询前的兜底：索引没就绪就先用种子 + 本地缓存建一次（秒级）。

    三条硬约束：
    1. 已就绪 → 立即返回，请求路径上零开销；
    2. 后台正在全量构建（build_all 握锁扫盘）→ **绝不抢锁**，沿用现有索引，
       宁可少引用几篇，也不能让提问卡在锁上；
    3. 并行任务下多个线程同时走到这里，锁内必须二次判断，
       否则会并发重建同一份索引（重建期间彼此清空对方的切片表）。
    """
    if _chunks and _STATE["phase"] == "ready":
        return
    warm_async()
    # 等预热线程**整体收尾**再放行（含向量索引），
    # 只看到 _chunks 非空就走会拿到「建了一半」的索引。
    _WARM_DONE.wait(wait)
    if _chunks:
        return                      # 已有索引（哪怕后台仍在增量构建）
    if _STATE["phase"] == "building":
        return                      # 手里没索引、后台又握着锁：宁可不引用也不卡住
    with _LOCK:
        if _chunks and _STATE["phase"] == "ready":
            return
        _rebuild()
        _STATE["phase"] = "ready"
        _STATE["message"] = f"已索引 {_STATE['total_chunks']} 片"


# ---------------- 检索 ----------------

# ---------------- 同义扩展（离线优先的检索增强）----------------
#
# 教学场景里同一个意思说法很多（"教学目标/学习目标/课时目标"、"作业/练习/题单"），
# 纯关键词匹配会漏召。这里用一张**可审阅的小词表**做同义扩展——零依赖、零联网、
# 教师能看懂也能改，代价是覆盖面有限，但比"为了召回把数据送到云端"更可接受。
_SYNONYMS = {
    "教学目标": ["学习目标", "课时目标", "目标叙写"],
    "学习目标": ["教学目标", "课时目标"],
    "课标": ["课程标准", "新课标", "课程目标"],
    "核心素养": ["素养", "关键能力", "必备品格"],
    "评价": ["评估", "评价标准", "量规", "评价任务"],
    "作业": ["练习", "题单", "任务单"],
    "导入": ["情境", "引课", "导入环节"],
    "支架": ["脚手架", "支持", "提示"],
    "分层": ["差异化", "层次", "基础进阶挑战"],
    "探究": ["问题链", "小组合作", "任务驱动"],
    "反思": ["复盘", "改进", "二次备课"],
    "学情": ["学生情况", "起点", "已有基础"],
}


def expand_query(query, max_extra=3):
    """查询扩展：先取**随包词向量**学出的近邻，再补手工同义词表。

    两个来源的分工：
    - 词向量近邻（embed）：从教材语料共现中学出来的，能给出
      "评价 → 反思、改进、总结" 这类我没预先写、但教学语境里相关的词；
    - 手工同义词表：覆盖词向量未必学到、但教学上必须等价的说法
      （如"教学目标 / 学习目标 / 课时目标"）。
    两者互补；词向量不可用时，手工表照常生效，功能不退化。
    """
    q = (query or "").strip()
    if not q:
        return ""
    extra = []
    try:
        from . import embed
        for w in embed.expand(q, per_word=1, max_extra=2):
            if w not in q and w not in extra:
                extra.append(w)
    except Exception:
        pass
    for term, syns in _SYNONYMS.items():
        if term in q:
            for s in syns:
                if s not in q and s not in extra:
                    extra.append(s)
                if len(extra) >= max_extra + 2:      # 词向量 + 词表合计上限
                    break
        if len(extra) >= max_extra + 2:
            break
    return q + (" " + " ".join(extra[:max_extra + 2]) if extra else "")


def semantic_cloud_enabled():
    """云端语义改写是否启用（默认关闭，须教师显式开启）。"""
    try:
        from . import config
        cur = config.current() or {}
        return bool(cur.get("semantic_cloud"))
    except Exception:
        return False


def cloud_rewrite(query, timeout=20):
    """云端语义改写（仅在教师显式开启时调用）。

    做的不是"把资料传到云端检索"，而是把教师的检索词改写成更完整的检索表达；
    **发送前先脱敏**，且不发送任何校本资料，只发查询词本身。
    任何异常都静默回退到离线扩展，绝不因联网失败阻塞本地检索。
    """
    if not query or not semantic_cloud_enabled():
        return None
    try:
        from . import guard
        if hasattr(guard, "desensitize"):
            safe = guard.desensitize(query)
            safe = safe[0] if isinstance(safe, (tuple, list)) else safe
        elif hasattr(guard, "redact_text"):
            safe = guard.redact_text(query)
        else:
            safe = query
        safe = safe or query
    except Exception:
        safe = query
    try:
        from . import llm
        client = llm.LLMClient()
        out = []
        for chunk in client.chat(
                [{"role": "system", "content":
                  "你是检索助手。把用户的检索词改写为 3~5 个中文检索关键词，"
                  "只输出关键词，用空格分隔，不要解释。"},
                 {"role": "user", "content": safe[:200]}],
                temperature=0.2, stream=True, demo_role=None, task="rewrite"):
            out.append(chunk)
            if len("".join(out)) > 200:
                break
        text = "".join(out).strip()
        return text[:200] if text else None
    except Exception:
        return None


def _split_keywords(query):
    """把中文查询切成关键词段（按标点切段 + 停用词过滤 + 有限 n-gram）。"""
    # 仅过滤虚词与量词；「设计」等领域词必须保留，否则「教学设计」被削成「教学」
    stop = {"的", "了", "和", "与", "是", "在", "请", "帮我", "一个", "一下",
            "关于", "如何", "怎么", "什么", "这个", "可以", "需要",
            "一些", "有哪", "哪些"}
    parts = re.split(r"[\s,，、。；;：:！!？?（）()《》「」“”\"'·\-—~]+", query)
    keywords = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        for w in stop:
            p = p.replace(w, "")
        if not p:
            continue
        if len(p) <= 6:
            keywords.append(p)
            continue
        if not re.search(r"[\u4e00-\u9fff]", p):
            # 纯西文/数字长词：整词匹配，切成 2~3 字碎片会命中大量无关英文文本
            keywords.append(p)
            continue
        else:
            # 长短语切 2~4 字滑动窗口（限量，保证检索速度）
            for size in (4, 3, 2):
                for i in range(0, min(len(p) - size + 1, 12)):
                    keywords.append(p[i:i + size])
    # 去重且限制总量
    seen, out = set(), []
    for k in keywords:
        if k not in seen and len(k) >= 2:
            seen.add(k)
            out.append(k)
    return out[:24]


def _bigram_candidates(query, kws):
    """向量召回的字面门控：返回与查询共享汉字片段的切片下标集合。

    用查询的 CJK 二字片段在长文本里定位（同关键词扫描，C 级速度）。
    返回 None 表示无法构造门控（如查询无汉字），此时不限制。
    """
    core = "".join(re.findall(r"[\u4e00-\u9fff]", query or ""))
    grams = {core[i:i + 2] for i in range(len(core) - 1)} if len(core) >= 2 else set()
    grams |= {k for k in (kws or []) if k and re.search(r"[\u4e00-\u9fff]", k)}
    grams = {g for g in grams if len(g) >= 2}
    if not grams or not _ALL_TEXT:
        return None
    out = set()
    for g in list(grams)[:40]:                     # 限量，避免长查询拖慢
        start, hits = 0, 0
        while hits < 400:
            idx = _ALL_TEXT.find(g, start)
            if idx < 0:
                break
            ci = bisect.bisect_right(_OFFSETS, idx) - 1
            if 0 <= ci < len(_chunks):
                out.add(ci)
            hits += 1
            start = idx + 1
    return out or None


def _search_chunks(query, topk):
    """切片检索：子串定位取候选 → BM25 精排。

    两阶段设计是为了兼顾速度与质量：
    1. 用 str.find（C 级扫描，毫秒级）定位候选切片，只记录命中的不同词；
    2. 仅对通过相关性门槛的候选计算 BM25（IDF × 词频饱和 × 长度归一化）。
    相比原先「按词长给固定权重 1/2/3 累加」，BM25 能压低高频通用词的影响、
    并按切片长度归一化，长切片不再因词多而虚高。
    """
    if not _ALL_TEXT or not _OFFSETS:
        return []
    # 快照：并行任务下 _chunks 可能被重建整体替换，全程用快照避免下标错乱
    chunks = list(_chunks)
    vs_cache = _vec_score()
    vs_cache.clear()
    kws = _split_keywords(query)
    terms = {}       # 切片 -> 命中的不同关键词集合（区分度依据）

    for kw in kws:
        start = 0
        hits = 0
        while hits < 2000:                      # 超高频词限量
            idx = _ALL_TEXT.find(kw, start)
            if idx < 0:
                break
            ci = bisect.bisect_right(_OFFSETS, idx) - 1
            if 0 <= ci < len(chunks):
                terms.setdefault(ci, set()).add(kw)
            hits += 1
            start = idx + 1

    # 相关性门槛（宁缺勿滥：噪声整片注入 Prompt 的代价高于漏召）
    #   强命中：≥3 个不同关键词命中
    #   弱命中：2 个关键词，且其中含 4 字以上长词（区分度高）
    picked = {ci for ci, ts in terms.items() if len(ts) >= 3}
    picked |= {ci for ci, ts in terms.items()
               if len(ts) == 2 and any(len(t) >= 4 for t in ts)}
    if not picked:
        # 兜底：多词共现门槛过严时，允许「单一稀有词」命中。
        # 典型场景是「为《背影》设计教学目标」——「背影」在语料中很稀有，
        # 但「教学目标」这类通用词几乎不与课题名同片，若要求共现就永远召回不到
        # 课题原文，教师反而拿不到依据。稀有词（df 很低）区分度足够，可放心召回。
        limit = max(30, int(_STATS["n"] / 20) or 1)
        picked = {ci for ci, ts in terms.items()
                  if len(ts) == 1 and _df(next(iter(ts))) <= limit}
    if not picked:
        return []

    # 向量召回补充候选：BM25 要求字面命中，同义改写会漏。
    # **关键门控**：只让"与查询真正共享汉字片段"的切片参与向量打分。
    # 不加这道门时，哈希碰撞会让教材样板页（版权/联系方式）与英文页
    # 长期占据"课时目标叙写"这类查询的相似榜首——它们与查询零共同特征。
    try:
        from . import vectors
        if vectors.enabled() and _VECTORS:
            gate = _bigram_candidates(query, kws)
            qv = vectors.query_vector(query)
            sims = []
            nz = [(i, x) for i, x in enumerate(qv) if x]
            for idx, v in enumerate(_VECTORS):
                if gate is not None and idx not in gate:
                    continue
                s = 0.0
                for i, x in nz:
                    s += x * v[i]
                if s >= 0.10:
                    sims.append((s, idx))
            sims.sort(key=lambda x: -x[0])
            top_sim = sims[0][0] if sims else 1.0
            for s, idx in sims[:max(topk, 5)]:
                if idx < len(chunks):          # 快照边界：脏下标直接丢弃
                    picked.add(idx)
                    vs_cache[idx] = s / (top_sim or 1.0)
    except Exception:
        pass

    idfs = {kw: _idf(kw) for kw in kws}
    raw = {}
    for ci in picked:
        if not (0 <= ci < len(chunks)):         # 快照边界：脏下标直接丢弃
            continue
        text = chunks[ci]["text"]
        dl = len(text)
        s = 0.0
        for kw in kws:
            tf = text.count(kw)                 # 词频同样走 C 级扫描
            if tf:
                s += _bm25(tf, dl, idfs[kw])
        vs = vs_cache.get(ci)
        if vs:                                  # 混合：BM25 为主，向量为辅
            s = s * (1 - _VEC_WEIGHT) + 10.0 * vs * _VEC_WEIGHT
        if _source_bonus(chunks[ci].get("source", ""), query):
            s *= 1.15                            # 同学科/学段来源轻微加权
        raw[ci] = s
    if not raw:
        return []
    vec = dict(vs_cache)        # 取快照后再清空，供后面的召回保底使用
    vs_cache.clear()

    # 归一到 0~10，便于与内置理论条目的命中分在同一量纲上比较
    top = max(raw.values()) or 1.0
    ranked = sorted(raw.items(), key=lambda x: -x[1])
    chosen = [ci for ci, _ in ranked[:topk]]

    # 召回保底：字面命中的切片会挤掉全部同义候选，导致"换个说法就搜不到"。
    # 因此当存在「只被向量召回」的切片时，至少保留 1 个（放在末位，不抢占强命中）。
    if vec:
        vec_only = [ci for ci in chosen if ci in vec
                    and len(terms.get(ci) or ()) == 0]
        if not vec_only:
            cand = [ci for ci, s in sorted(vec.items(), key=lambda x: -x[1])
                    if len(terms.get(ci) or ()) == 0]
            if cand and cand[0] not in chosen:
                chosen = chosen[:-1] + [cand[0]]
    out = []
    for ci in chosen:
        if ci not in raw or not (0 <= ci < len(chunks)):
            continue                            # 保底候选可能来自向量召回，未必在 raw 里
        out.append((round(10.0 * raw[ci] / top, 4), chunks[ci]))
    return out


def search_hits(query, topk=3, prefer=None):
    """检索并返回结构化命中项：[{"text","src","cid","source"}, ...]。

    带 cid 是为了**引用可追溯**：注入上下文时连同 [来源:cid] 一起给模型，
    模型引用了哪一片可被校验，教师也能点开看原文。

    召回增强（Phase 2.2）：先做**离线同义扩展**（词表可审阅、零联网）；
    若教师显式开启了云端语义改写，再叠加改写词。两者都只是"把查询说得更全"，
    检索本身仍完全在本地完成。
    """
    _ensure_loaded()
    if not query:
        return []
    results = []
    seen = set()

    queries = [query]
    expanded = expand_query(query)
    if expanded and expanded != query:
        queries.append(expanded)
    if semantic_cloud_enabled():
        rewritten = cloud_rewrite(query)
        if rewritten:
            queries.append(rewritten)

    # 本地切片（带来源，权重高）：多版查询各取 topk，按分数合并去重
    for qi, q in enumerate(queries):
        bias = 0 if qi == 0 else -0.5       # 原查询优先，扩展词降权
        for score, chunk in _search_chunks(q, topk):
            cid = chunk.get("id", "")
            if cid in seen:
                continue
            seen.add(cid)
            src = "【%s】" % chunk.get("source", "")
            results.append((score + 2 + bias, {
                "text": chunk.get("text", ""), "src": src,
                "cid": cid,
                "source": chunk.get("source", ""),
            }))

    # 内置理论条目
    kws = _split_keywords(query)
    prefer = set(prefer or ())
    for item in KNOWLEDGE:
        haystack = f"{item['cat']} {' '.join(item['tags'])} {item['text']}"
        score = sum(1 for kw in kws if kw in haystack)
        if score > 0:
            if prefer & set(item.get("tags") or ()):
                score += 2                     # 专家偏好命中，加权提升
            results.append((score, {
                "text": item["text"],
                "src": "【%s】" % item["cat"],
                "cid": item.get("id", ""),
                "source": "理论·" + (item.get("cat") or ""),
            }))

    results.sort(key=lambda x: -x[0])
    return [h for _, h in results[:topk]]


def search(query, topk=3, prefer=None):
    """检索（沿用旧的 (文本, 来源) 二元组签名，供既有调用方使用）。"""
    return [(h["text"], h["src"]) for h in search_hits(query, topk, prefer)]


def render(query, topk=3, prefer=None):
    """渲染为可注入 System Prompt 的知识块（带 [来源:cid] 引用标记）。"""
    hits = search_hits(query, topk, prefer=prefer)
    if not hits:
        return ""
    lines = ["# 知识库参考（本地检索，仅供教学参考）",
             "# 引用规范：凡依据下列资料作出的论断，请在其后标注 [来源:cid]，"
             "cid 必须原样取自下方；不得凭空编造 cid。"]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. [来源:{h['cid']}] {h['src']} {h['text']}")
    return "\n".join(lines)


# ---------------- 引用可追溯 ----------------

_CITE_RE = re.compile(r"\[来源[:：]\s*([0-9a-zA-Z_\-]{4,32})\s*\]")


def get_chunk(cid):
    """按引用 ID 取回单条原文（只返回该条，不暴露整个知识库）。"""
    c = _CHUNK_BY_ID.get(cid or "")
    if not c:
        return None
    return {"cid": cid, "source": c.get("source", ""), "text": c.get("text", "")}


def validate_citations(text):
    """校验产出中的引用标记。返回 (合法ID列表, 无效ID列表)。

    无效 ID 说明模型编造了出处——这正是教育场景最危险的情况（假引用比不引用
    更有迷惑性），必须剔除标记并提示教师核对。
    """
    if not text:
        return [], []
    valid, invalid = [], []
    for cid in _CITE_RE.findall(text):
        (valid if cid in _CHUNK_BY_ID else invalid).append(cid)
    return valid, invalid


def strip_bad_citations(text):
    """剔除无效引用标记（保留正文），避免假出处留在给教师看的文本里。"""
    if not text:
        return text
    return _CITE_RE.sub(lambda m: ("" if m.group(1) not in _CHUNK_BY_ID
                                   else m.group(0)), text)
