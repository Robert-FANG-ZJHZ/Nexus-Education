"""图表 SVG 生成与导出（零第三方依赖）。

补齐路线图 1.4「输出多形态」：Word / PPT 之外，把数据渲染成矢量图 SVG。

设计取舍：
- 零依赖：不引入 matplotlib / cairo，纯字符串拼装 + 标准库 math，保持与
  pptxgen / docxgen 一致的"零第三方依赖"工程约束，打包体积不增加。
- 确定性：同样输入必得同样 SVG（不随机取色、不写时间戳），A/B 与回归可比。
- 安全：所有文本经 XML 转义，杜绝标签注入；数值统一收敛为有限浮点数。
- 可读：坐标保留 2 位小数，刻度取 1/2/2.5/5 × 10^n，避免刻度是 37.5 这种数。

支持三种图：bar 柱状 / line 折线 / pie 饼图。
"""

import math
import os

from xml.sax.saxutils import escape

# 画布与配色（与前端品牌色一致）
_DEFAULT_W = 720
_DEFAULT_H = 420
_PALETTE = ("#0E7490", "#0F766E", "#B45309", "#7C3AED", "#BE123C",
            "#15803D", "#1D4ED8", "#A16207", "#0EA5E9", "#65A30D")
_FONT = "'Microsoft YaHei','PingFang SC','Hiragino Sans GB',sans-serif"

_M = {"l": 68.0, "r": 26.0, "t": 58.0, "b": 68.0}   # 四周留白
_GRID = "#E8EAF2"
_AXIS = "#9AA2BC"
_TEXT = "#1C2642"
_MUTED = "#6E7695"


def _esc(t):
    """XML 转义：文本一律不可信（可能来自模型或用户输入）。"""
    return escape("" if t is None else str(t), {'"': "&quot;", "'": "&apos;"})


def _num(v):
    """收敛为有限浮点数，非数字按 0 处理（NaN/inf 会污染 SVG 坐标）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def _nice_ceil(v):
    """轴上限抬到 1/2/2.5/5 × 10^n，保证刻度好读。"""
    if v <= 0:
        return 1.0
    exp = math.floor(math.log10(v))
    base = 10.0 ** exp
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if v <= m * base + 1e-9:
            return m * base
    return 10.0 * base


def _fmt(v):
    """刻度文本：整数不带小数点，小数最多两位。"""
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return ("%.2f" % v).rstrip("0").rstrip(".")


def _svg_open(w, h, title):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="%s">\n'
        '  <rect width="%d" height="%d" fill="#FFFFFF"/>\n'
        % (w, h, w, h, _esc(_FONT), w, h)
        + ('  <text x="%.1f" y="30" text-anchor="middle" font-size="16" '
           'font-weight="600" fill="%s">%s</text>\n'
           % (w / 2.0, _TEXT, _esc(title)) if title else "")
    )


def _frame(w, h, title, x_title="", y_title=""):
    """标题 + 可选轴名，返回 (SVG 前缀, 绘图区左上角与尺寸)。"""
    pw = w - _M["l"] - _M["r"]
    ph = h - _M["t"] - _M["b"]
    out = [_svg_open(w, h, title)]
    if y_title:
        out.append('  <text x="14" y="%.1f" font-size="11" fill="%s" '
                   'text-anchor="middle" transform="rotate(-90 14 %.1f)">%s</text>\n'
                   % (_M["t"] + ph / 2.0, _MUTED, _M["t"] + ph / 2.0, _esc(y_title)))
    if x_title:
        out.append('  <text x="%.1f" y="%.1f" font-size="11" fill="%s" '
                   'text-anchor="middle">%s</text>\n'
                   % (_M["l"] + pw / 2.0, h - 16.0, _MUTED, _esc(x_title)))
    return out, _M["l"], _M["t"], pw, ph


def bar(labels, values, title="", x_title="", y_title="",
        w=_DEFAULT_W, h=_DEFAULT_H):
    """垂直柱状图。labels 为类目名，values 为对应数值。"""
    vals = [_num(v) for v in (values or [])]
    labs = [str(x) for x in (labels or [])]
    n = min(len(vals), len(labs))
    vals, labs = vals[:n], labs[:n]

    out, x0, y0, pw, ph = _frame(w, h, title, x_title, y_title)
    if n == 0:
        out.append(_empty(w, h))
        return "".join(out) + "</svg>\n"

    lo = min(0.0, min(vals))
    hi = max(0.0, max(vals))
    top = _nice_ceil(hi) if hi > 0 else 1.0
    bot = -_nice_ceil(-lo) if lo < 0 else 0.0
    span = (top - bot) or 1.0

    def y_of(v):
        return y0 + ph - (v - bot) / span * ph

    # 横向网格 + Y 轴刻度（含 0 线）
    for i in range(5):
        v = bot + span * i / 4.0
        y = y_of(v)
        out.append('  <line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                   'stroke="%s" stroke-width="1"/>\n'
                   % (x0, y, x0 + pw, y, _AXIS if abs(v) < 1e-9 else _GRID))
        out.append('  <text x="%.1f" y="%.1f" font-size="10.5" fill="%s" '
                   'text-anchor="end">%s</text>\n'
                   % (x0 - 8.0, y + 3.5, _MUTED, _fmt(v)))

    slot = pw / n
    bw = min(slot * 0.62, 64.0)
    for i, v in enumerate(vals):
        cx = x0 + slot * (i + 0.5)
        yv = y_of(v)
        yb = y_of(0.0)
        top_y, bot_y = (yv, yb) if v >= 0 else (yb, yv)
        hh = max(bot_y - top_y, 0.5)
        color = _PALETTE[i % len(_PALETTE)]
        out.append('  <rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                   'rx="3" fill="%s"/>\n'
                   % (cx - bw / 2.0, top_y, bw, hh, color))
        # 数值标签贴在柱子外侧，避免与柱子重叠
        ly = top_y - 6.0 if v >= 0 else bot_y + 13.0
        out.append('  <text x="%.1f" y="%.1f" font-size="10.5" fill="%s" '
                   'text-anchor="middle">%s</text>\n'
                   % (cx, ly, _TEXT, _fmt(v)))
        out.append('  <text x="%.1f" y="%.1f" font-size="11" fill="%s" '
                   'text-anchor="middle">%s</text>\n'
                   % (cx, y0 + ph + 18.0, _MUTED, _esc(labs[i])))
    return "".join(out) + "</svg>\n"


def line(labels, values, title="", x_title="", y_title="",
         w=_DEFAULT_W, h=_DEFAULT_H):
    """折线图：单点时退化为一个圆点，避免 polyline 无内容。"""
    vals = [_num(v) for v in (values or [])]
    labs = [str(x) for x in (labels or [])]
    n = min(len(vals), len(labs))
    vals, labs = vals[:n], labs[:n]

    out, x0, y0, pw, ph = _frame(w, h, title, x_title, y_title)
    if n == 0:
        out.append(_empty(w, h))
        return "".join(out) + "</svg>\n"

    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:                      # 全平：给一个对称区间，线画在中间
        pad = abs(hi) if abs(hi) > 1e-9 else 1.0
        lo, hi = lo - pad, hi + pad
    top = _nice_ceil(hi)
    bot = -_nice_ceil(-lo) if lo < 0 else (0.0 if lo >= 0 else lo)
    span = (top - bot) or 1.0

    def y_of(v):
        return y0 + ph - (v - bot) / span * ph

    for i in range(5):
        v = bot + span * i / 4.0
        y = y_of(v)
        out.append('  <line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                   'stroke="%s" stroke-width="1"/>\n'
                   % (x0, y, x0 + pw, y, _AXIS if abs(v) < 1e-9 else _GRID))
        out.append('  <text x="%.1f" y="%.1f" font-size="10.5" fill="%s" '
                   'text-anchor="end">%s</text>\n'
                   % (x0 - 8.0, y + 3.5, _MUTED, _fmt(v)))

    step = pw / max(n - 1, 1)
    pts = []
    for i, v in enumerate(vals):
        px = x0 + (pw / 2.0 if n == 1 else step * i)
        py = y_of(v)
        pts.append((px, py))
        out.append('  <text x="%.1f" y="%.1f" font-size="11" fill="%s" '
                   'text-anchor="middle">%s</text>\n'
                   % (px, y0 + ph + 18.0, _MUTED, _esc(labs[i])))

    if n > 1:
        d = " ".join("%.1f,%.1f" % p for p in pts)
        out.append('  <polyline points="%s" fill="none" stroke="%s" '
                   'stroke-width="2.4" stroke-linejoin="round" '
                   'stroke-linecap="round"/>\n' % (d, _PALETTE[0]))
    for px, py in pts:
        out.append('  <circle cx="%.1f" cy="%.1f" r="3.6" fill="#FFFFFF" '
                   'stroke="%s" stroke-width="2.2"/>\n' % (px, py, _PALETTE[0]))
    return "".join(out) + "</svg>\n"


def _arc(cx, cy, r, a0, a1):
    """饼图扇形的 path（不含圆心连线，便于做圆环；此处用实心扇形）。"""
    x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
    x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
    large = 1 if (a1 - a0) > math.pi else 0
    return ("M %.2f %.2f L %.2f %.2f A %.2f %.2f 0 %d 1 %.2f %.2f Z"
            % (cx, cy, x0, y0, r, r, large, x1, y1))


def pie(labels, values, title="", w=_DEFAULT_W, h=_DEFAULT_H):
    """饼图。总和为 0 时退化为提示文本，避免除零与角度 NaN。"""
    vals = [_num(v) for v in (values or [])]
    labs = [str(x) for x in (labels or [])]
    n = min(len(vals), len(labs))
    vals, labs = vals[:n], labs[:n]

    out = [_svg_open(w, h, title)]
    if n == 0:
        out.append(_empty(w, h))
        return "".join(out) + "</svg>\n"

    total = sum(abs(v) for v in vals)
    if total <= 1e-9:
        out.append(_empty(w, h, "数值总和为 0，无法成图"))
        return "".join(out) + "</svg>\n"

    cx = w / 2.0
    cy = _M["t"] + (h - _M["t"] - _M["b"]) / 2.0
    r = min(w, h) * 0.30
    legend_w = 132.0
    if w > 420:                     # 留出图例空间时把圆心左移
        cx = (w - legend_w) / 2.0 + 6.0

    a = -math.pi / 2.0              # 从 12 点方向开始，符合阅读习惯
    for i, v in enumerate(vals):
        frac = abs(v) / total
        sweep = frac * math.pi * 2.0
        out.append('  <path d="%s" fill="%s" stroke="#FFFFFF" '
                   'stroke-width="1.6"/>\n'
                   % (_arc(cx, cy, r, a, a + sweep), _PALETTE[i % len(_PALETTE)]))
        a += sweep

    # 图例：色块 + 名称 + 占比
    a = -math.pi / 2.0
    ly = cy - (n * 20.0) / 2.0 + 12.0
    for i, v in enumerate(vals):
        frac = abs(v) / total
        lx = w - legend_w + 4.0
        out.append('  <rect x="%.1f" y="%.1f" width="11" height="11" rx="2.5" '
                   'fill="%s"/>\n' % (lx, ly - 9.0, _PALETTE[i % len(_PALETTE)]))
        out.append('  <text x="%.1f" y="%.1f" font-size="11" fill="%s">%s '
                   '%.1f%%</text>\n'
                   % (lx + 17.0, ly, _TEXT, _esc(labs[i]), frac * 100.0))
        ly += 20.0
    return "".join(out) + "</svg>\n"


def _empty(w, h, msg="暂无数据"):
    return ('  <text x="%.1f" y="%.1f" font-size="13" fill="%s" '
            'text-anchor="middle">%s</text>\n'
            % (w / 2.0, h / 2.0, _MUTED, _esc(msg)))


def render(spec):
    """按 spec 渲染 SVG，返回字符串。

    spec: {"type": bar|line|pie, "title", "labels":[], "values":[],
           "x_title", "y_title", "width", "height"}
    未知类型按 bar 处理，保证调用方不会因为拼错字段名而崩。
    """
    spec = spec if isinstance(spec, dict) else {}
    kind = str(spec.get("type") or "bar").strip().lower()
    labels = spec.get("labels") or []
    values = spec.get("values") or []
    title = spec.get("title") or ""
    w = int(_num(spec.get("width")) or _DEFAULT_W)
    h = int(_num(spec.get("height")) or _DEFAULT_H)
    w = max(240, min(w, 4000))
    h = max(200, min(h, 4000))

    if kind == "line":
        return line(labels, values, title, spec.get("x_title") or "",
                    spec.get("y_title") or "", w, h)
    if kind == "pie":
        return pie(labels, values, title, w, h)
    return bar(labels, values, title, spec.get("x_title") or "",
               spec.get("y_title") or "", w, h)


def save(spec, out_dir, name="chart"):
    """渲染并写入 <out_dir>/<name>.svg，返回文件绝对路径。"""
    safe = "".join(c for c in str(name or "chart")
                   if c not in '/\\:*?"<>|').strip()[:60] or "chart"
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, safe + ".svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(spec))
    return path


def bytes_of(spec):
    """返回可直接下发的 UTF-8 字节（供 /api/export/svg 使用）。"""
    return render(spec).encode("utf-8")
