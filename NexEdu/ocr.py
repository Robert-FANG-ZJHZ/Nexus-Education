"""扫描版 PDF / 图片 OCR（设计文档 Phase 3.3，优先做 OCR 的原因见下）。

为什么先做 OCR：扫描版教科书与试卷图片**提取不到任何文字**，知识库会直接少一块
资料，而这是教师真实会遇到的情况（学校发的电子教材很多是扫描件）。

诚实的边界：本项目坚持核心功能零第三方依赖，而 OCR 这件事**不可能**用标准库实现。
因此这里的取舍是：
- **不打包**任何 OCR 引擎（体积大、许可复杂）；
- 检测本机是否已装 `tesseract`（图片 OCR）与 `pdftoppm`（PDF 转图，poppler 提供）；
- 有就调用，没有就**明确告诉教师缺什么、装了能做什么**，而不是假装成功。

也就是说：它是"接得上线就用、接不上就说实话"的可选能力，不是核心依赖。
教师数据不出本地——OCR 全程在本机进程完成，不上传任何页面。
"""

import os
import shutil
import subprocess
import tempfile

# 单份文档最多 OCR 多少页：扫描教材动辄几百页，全跑既慢又没必要
MAX_PAGES = 30
_LANG = "chi_sim+eng"


def engines():
    """检测本机可用的 OCR 相关命令。"""
    return {
        "tesseract": shutil.which("tesseract"),
        "pdftoppm": shutil.which("pdftoppm"),   # poppler：PDF → 图片
    }


def available():
    e = engines()
    return bool(e.get("tesseract"))


def status():
    e = engines()
    if e["tesseract"]:
        msg = "已检测到本地 OCR 引擎 tesseract"
        if not e["pdftoppm"]:
            msg += "（未检测到 pdftoppm，扫描版 PDF 需它先转成图片）"
        return {"ok": True, "message": msg, "engines": e}
    return {"ok": False,
            "message": "未检测到本地 OCR 引擎（tesseract）。扫描版 PDF 与试卷图片"
                       "将只能给出提示；安装 tesseract 后本功能自动可用。",
            "engines": e}


def is_scanned_pdf(path, min_chars=50):
    """判断是否扫描版 PDF（文本层几乎为空）。无法判定时返回 False。"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        text = []
        for page in reader.pages[:5]:
            try:
                text.append(page.extract_text() or "")
            except Exception:
                continue
        return len("".join(text).strip()) < min_chars
    except Exception:
        return False


def _run(cmd, timeout=120):
    try:
        done = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="ignore")
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return ""
    return (done.stdout or "").strip()


def ocr_image(path, lang=_LANG):
    """OCR 单张图片。返回 (文本, 错误信息)。"""
    if not os.path.exists(path):
        return "", "文件不存在"
    tess = engines().get("tesseract")
    if not tess:
        return "", "未检测到 tesseract，无法识别图片"
    text = _run([tess, path, "stdout", "-l", lang, "--psm", "6"])
    return (text, "") if text else ("", "OCR 未识别到文字（图片可能过小或过淡）")


def ocr_pdf(path, max_pages=MAX_PAGES, lang=_LANG):
    """OCR 扫描版 PDF。返回 (文本, 错误信息)。

    流程：pdftoppm 转成 PNG → 逐页 tesseract → 拼接。
    任何一步缺组件都给出明确原因，不静默失败。
    """
    if not os.path.exists(path):
        return "", "文件不存在"
    eng = engines()
    if not eng.get("tesseract"):
        return "", "未检测到 tesseract，无法 OCR"
    if not eng.get("pdftoppm"):
        return "", "未检测到 pdftoppm（poppler），无法把 PDF 转成图片"

    out = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = os.path.join(tmp, "page")
            _run([eng["pdftoppm"], "-r", "200", "-png", "-f", "1",
                  "-l", str(max_pages), path, prefix])
            pages = sorted([f for f in os.listdir(tmp) if f.endswith(".png")])
            for fn in pages:
                t, err = ocr_image(os.path.join(tmp, fn), lang)
                if t:
                    out.append(t)
    except Exception as e:
        return "", f"OCR 失败：{e}"
    if not out:
        return "", "未识别到文字"
    return "\n\n".join(out), ""


def try_extract(path):
    """给 knowledge 用的统一入口：文本层 → 不够则 OCR。

    返回 (文本, 说明)。说明用于界面提示，让教师知道发生了什么。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"):
        text, err = ocr_image(path)
        return text, (err or "已 OCR 识别图片文字")
    if ext == ".pdf" and is_scanned_pdf(path):
        text, err = ocr_pdf(path)
        if text:
            return text, "扫描版 PDF，已本地 OCR 提取"
        return "", err or "扫描版 PDF 无可用 OCR 结果"
    return "", ""
