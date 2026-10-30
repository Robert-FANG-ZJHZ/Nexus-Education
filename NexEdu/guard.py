"""防爬取与知识产权保护（内置知识库 / 专家指令）。

设计目标：内置知识库与专家系统指令是项目组原创核心资产，需防止被
「通过用户端交互」批量搬走。本模块提供三类本地防护，零第三方依赖：

1. **防批量爬取**（`check_rate` / `detect_scraping`）
   - 生成类接口（对话 / 智能体 / 一键备课）做滑动窗口限流；
   - 识别「枚举 / 批量导出知识库内容」的意图并拦截；
   - 反复提交近乎相同的请求也会被识别为抓取行为。

2. **防提示词泄露**（`register_prompts` / `StreamRedactor`）
   - 把内置提示词切分为「指纹片段」；
   - 在**流式输出**上做实时脱敏：即便模型被诱导复述系统指令，
     界面也只会看到「〔内部指令已隐藏〕」，不会显示原文。

3. **防明文读取**（`obfuscate` / `deobfuscate`）
   - 提示词默认不再以明文写入磁盘；确需文件化定制时以**加密**形式落盘，
     需显式开启（见 agents.export_default_prompts）。
   - V2 用 AES-256-CTR + HMAC-SHA256（Encrypt-then-MAC），密钥派生自本机
     机器特征 —— 既不可直接阅读，**拷到其它机器也解不开**；
     V1 旧格式（zlib+Base64）仍可读取，再次导出时自动升级。

边界说明：以上均为**提高搬取成本**的工程手段，而非密码学意义上的不可破解。
离线产品中，内置资源本身随 exe 分发，无法对拥有者做绝对保密；本模块针对的是
「借对话接口批量抽取」这一最主要的现实风险，并对提示词做输出侧兜底。
"""

import base64
import logging
import os
import re
import threading
import time
import zlib

from . import _crypto

_log = logging.getLogger("nexedu.guard")

# ---------------- 审计（并入 safety 的统一审计流） ----------------

def _audit(kind, detail):
    try:
        from . import safety
        safety.log_event(kind, detail)
    except Exception:
        _log.warning("guard[%s] %s", kind, detail)


# ================= 1. 限流 =================

_RATE_WINDOW = 60.0     # 统计窗口（秒）
_RATE_MAX = 30          # 窗口内允许的生成类请求数（0 = 关闭限流）

_rate_hits = []
_rate_lock = threading.Lock()

# 近乎相同请求的重复检测
_REPEAT_MAX = 6         # 窗口内同一请求最多重复次数
_repeat_recent = []     # [(ts, fingerprint)]

# 数据/资产类接口（知识库切片、技能、专家目录…）单独分桶限流
_API_RATE_MAX = 120     # 窗口内允许的数据类请求数
_api_hits = {}          # bucket -> [ts]


def reset_rate():
    """清空限流状态（测试用）。"""
    with _rate_lock:
        _rate_hits.clear()
        _repeat_recent.clear()
        _api_hits.clear()


def _rate_max():
    raw = os.environ.get("NEXEDU_RATE_MAX")
    if raw is None:
        return _RATE_MAX
    try:
        return int(raw)
    except ValueError:
        return _RATE_MAX


def check_rate():
    """滑动窗口限流。返回 (是否放行, 说明)。"""
    maxn = _rate_max()
    if maxn <= 0:
        return True, ""
    now = time.time()
    with _rate_lock:
        while _rate_hits and now - _rate_hits[0] > _RATE_WINDOW:
            _rate_hits.pop(0)
        if len(_rate_hits) >= maxn:
            _audit("rate", "%ds 内超过 %d 次生成请求" % (int(_RATE_WINDOW), maxn))
            return False, ("操作过于频繁，已触发访问限流（%d 秒内最多 %d 次）。"
                           "请稍候再试。" % (int(_RATE_WINDOW), maxn))
        _rate_hits.append(now)
    return True, ""


# ================= 2. 爬取意图检测 =================

# 枚举动词 → 知识库目标词（动词在前）
_SCRAPE_VERB = (r"(列出|罗列|枚举|穷举|导出|打印|逐条|逐一|挨个|依次|"
                r"原样|逐字|一字不差|复述|贴出|搬运)")
_SCRAPE_TARGET = (r"(知识库|语料库|语料|切片|片段|知识条目|检索结果|参考资料|"
                  r"内置资料|内置内容|教科书原文|教材原文|案例原文|来源|出处)")

_PAT_VERB_THEN_TARGET = re.compile(
    _SCRAPE_VERB + r"[^。；;\n]{0,12}" + _SCRAPE_TARGET)
_PAT_TARGET_THEN_DUMP = re.compile(
    _SCRAPE_TARGET + r"[^。；;\n]{0,12}(全部|所有|完整|原样|逐字|一字不差|依次|逐条|挨个)"
    r"[^。；;\n]{0,10}(输出|列出|导出|打印|给出|贴出|返回|复述)")
_SCRAPE_STRONG = [
    (r"第\s*\d+\s*[条片页个批]", "疑似按序号逐批索取"),
    (r"(继续|接着|下一[条片页个批])[^。；;\n]{0,6}(输出|列出|给出|内容|原文)",
     "疑似连续翻页索取"),
    (r"\b(dump|enumerate|scrape)\b", "疑似批量导出指令"),
]


def _fingerprint(text):
    t = re.sub(r"\s+", "", text or "")
    return t[:120]


def check_api_rate(bucket="asset", maxn=None):
    """数据/资产类接口的单独限流（分桶）。

    知识库切片、技能、专家目录、工作流、教案列表等接口本身不产生 LLM 开销，
    若不限流，第三方可以按 id 逐个遍历把内置资产搬空；这里按桶分别计数。
    """
    limit = maxn if isinstance(maxn, int) and maxn > 0 else _API_RATE_MAX
    now = time.time()
    with _rate_lock:
        hits = _api_hits.setdefault(bucket, [])
        while hits and now - hits[0] > _RATE_WINDOW:
            hits.pop(0)
        if len(hits) >= limit:
            _audit("api_rate", "%s：%ds 内超过 %d 次" %
                   (bucket, int(_RATE_WINDOW), limit))
            return False, ("本地数据接口访问过于频繁（%d 秒内最多 %d 次），已暂时拒绝，"
                           "请稍候再试。" % (int(_RATE_WINDOW), limit))
        hits.append(now)
    return True, ""


def detect_scraping(text):
    """识别「批量抽取知识库」意图。返回 (是否可疑, 说明)。"""
    if not text:
        return False, ""
    t = text.strip()
    for pat, desc in _SCRAPE_STRONG:
        if re.search(pat, t, re.I):
            _audit("scrape", desc + "：" + t[:60])
            return True, desc
    if _PAT_VERB_THEN_TARGET.search(t) or _PAT_TARGET_THEN_DUMP.search(t):
        _audit("scrape", "枚举/批量导出知识库：" + t[:60])
        return True, "枚举或批量导出知识库内容"

    # 近乎相同的请求反复提交 → 判定为抓取倾向
    now = time.time()
    fp = _fingerprint(t)
    with _rate_lock:
        while _repeat_recent and now - _repeat_recent[0][0] > _RATE_WINDOW:
            _repeat_recent.pop(0)
        n = sum(1 for _, f in _repeat_recent if f == fp)
        _repeat_recent.append((now, fp))
        if n >= _REPEAT_MAX:
            _audit("scrape", "重复请求 %d 次：%s" % (n + 1, t[:40]))
            return True, "同一请求被反复提交"
    return False, ""


# ================= 3. 提示词指纹 + 流式脱敏 =================

_HIDDEN = "〔内部指令已隐藏〕"
_FP_MIN = 14            # 指纹片段最短长度（过短易误伤，不纳入）

_fingerprints = []      # 指纹片段（按长度降序）
_fp_prefixes = set()    # 所有指纹的真前缀（用于安全回吐判定）
_fp_ready = False


def register_prompts(texts, exclude_texts=()):
    """登记内置提示词的可识别片段（供输出脱敏）。

    texts：提示词全文列表；exclude_texts：不应误伤的文本（如知识库原文），
    其中出现过的片段会被剔除。
    """
    global _fingerprints, _fp_prefixes, _fp_ready
    exclude = "\n".join(exclude_texts or ())
    fps, seen = [], set()
    for text in texts:
        for line in re.split(r"[\n]+", text or ""):
            line = line.strip()
            if len(line) < _FP_MIN or line in seen:
                continue
            if line in exclude:        # 与知识库等正常内容重合的片段不脱敏
                continue
            seen.add(line)
            fps.append(line)
    fps.sort(key=len, reverse=True)
    pref = set()
    for fp in fps:
        for k in range(1, len(fp)):
            pref.add(fp[:k])
    _fingerprints = fps
    _fp_prefixes = pref
    _fp_ready = True
    if fps:
        _log.info("提示词指纹已登记：%d 条", len(fps))
    return len(fps)


def ready():
    return _fp_ready


def redact_text(text):
    """一次性脱敏（非流式场景使用）。"""
    if not text or not _fingerprints:
        return text
    out = text
    for fp in _fingerprints:
        if fp in out:
            out = out.replace(fp, _HIDDEN)
    return out


class StreamRedactor:
    """流式脱敏器：边收边脱敏，只扣住「可能是指纹前缀」的尾部，不牺牲流式观感。"""

    def __init__(self):
        self._buf = ""

    def _safe_tail(self):
        """返回必须扣住的最长尾部长度（该尾部是某个指纹的前缀）。"""
        limit = min(len(self._buf), max((len(f) - 1 for f in _fingerprints), default=0))
        for k in range(limit, 0, -1):
            if self._buf[-k:] in _fp_prefixes:
                return k
        return 0

    def feed(self, text):
        if not _fingerprints:
            return text
        self._buf += text
        out = []
        while True:
            hit_fp, hit_idx = "", -1
            for fp in _fingerprints:
                i = self._buf.find(fp)
                if i >= 0 and (hit_idx < 0 or i < hit_idx):
                    hit_idx, hit_fp = i, fp
            if hit_idx >= 0:
                out.append(self._buf[:hit_idx])
                out.append(_HIDDEN)
                self._buf = self._buf[hit_idx + len(hit_fp):]
                continue
            keep = self._safe_tail()
            if len(self._buf) > keep:
                out.append(self._buf[:len(self._buf) - keep] if keep else self._buf)
                self._buf = self._buf[len(self._buf) - keep:] if keep else ""
            break
        return "".join(out)

    def flush(self):
        """生成结束时吐出剩余缓冲（并做一次兜底替换）。"""
        t = self._buf
        self._buf = ""
        return redact_text(t)


# ================= 4. 工具观察事件脱敏 =================

def sanitize_observe(name, text):
    """把工具「观察」事件中对客户端可见的文本做脱敏。

    search_knowledge 的检索原文只应进入模型上下文，不在界面回显，
    否则界面本身就成了批量抓取知识库的通道。
    """
    if name == "search_knowledge":
        s = text or ""
        if "未检索到" in s:
            return "已检索本地知识库：未命中相关内容。"
        m = re.search(r"检索到\s*(\d+)\s*条", s)
        if m:
            return ("已检索本地知识库，命中 %s 条参考（原文已注入模型上下文，"
                    "不在界面展示，以防被批量抓取）。" % m.group(1))
        return "已检索本地知识库，结果已注入模型上下文。"
    return text


# ================= 5. 提示词落盘混淆 =================

_MAGIC = "NEXEDU-PROMPT-V1:"      # 旧格式（zlib + Base64）：仅保留读取兼容
_MAGIC_V2 = "NEXEDU-PROMPT-V2:"   # 现行：AES-256-CTR + HMAC-SHA256，密钥绑定本机


def _crypto_keys():
    """本机绑定的 (加密密钥, 认证密钥)。

    密钥材料派生自本机机器特征（Windows MachineGuid 等），因此**换机即变**：
    密文被拷到其它机器无法还原，被篡改也会被 HMAC 检出。
    需要多机共享同一份密文时，设环境变量 NEXEDU_CRYPTO_SALT 指定公共盐。
    """
    salt = (os.environ.get("NEXEDU_CRYPTO_SALT") or "").encode("utf-8")
    return _crypto.derive_keys(_crypto.device_material(salt))


def obfuscate(text):
    """把提示词加密为不可直接阅读、且**仅本机可解**的字符串。

    V2 格式：Base64( nonce(12) || AES-256-CTR 密文 || HMAC-SHA256(32) )。
    注意这是"提高读取成本 + 离机即废"的工程手段，不是密码学不可破解保证。
    """
    raw = (text or "").encode("utf-8")
    k, mk = _crypto_keys()
    return _MAGIC_V2 + base64.b64encode(_crypto.encrypt(k, mk, raw)).decode("ascii")


def deobfuscate(text):
    """还原提示词。

    - V2（现行）：先校验 HMAC 再解密，换机或被篡改返回空串；
    - V1（旧）：zlib + Base64，仍可读（再次导出时会升级为 V2）；
    - 明文：原样返回，兼容手工编辑。
    """
    t = (text or "").strip()
    if t.startswith(_MAGIC_V2):
        try:
            k, mk = _crypto_keys()
            return _crypto.decrypt(k, mk, base64.b64decode(t[len(_MAGIC_V2):])).decode("utf-8")
        except Exception:
            return ""
    if t.startswith(_MAGIC):
        try:
            return zlib.decompress(base64.b64decode(t[len(_MAGIC):])).decode("utf-8")
        except Exception:
            return ""
    return t
