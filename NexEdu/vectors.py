"""轻量本地向量检索（Phase 2.2 的离线方案，补齐"真向量召回"这块地基）。

诚实的定位：这是**哈希向量（hashing trick）+ 余弦相似度**，不是神经嵌入模型。
- 维度固定 256，字符 bigram + 词语 unigram 混合特征，tf 次线性化后 L2 归一；
- 对"同义改写、词序变化、部分拼写差异"有明确改善，**但做不到**语义层面的
  "教师说'减负'能召回'作业量控制'"这种跨词联想；
- 零第三方依赖、无需下载模型、索引随知识库重建，冷启动 ~毫秒级。

为什么不用真嵌入模型：离线合规不允许默认上云；随包分发一个小 ONNX 模型会让
安装包增大几十 MB 且冷启动变慢。这里先用地基方案解决 80% 的同义召回，
真嵌入作为可选组件留接口（`SEMANTIC_LOCAL` 关闭时完全不参与计算）。

与 BM25 的关系：**混合检索**——BM25 负责精确词命中，向量负责模糊召回，
两者分数归一后加权相加（权重可在设置中调），任一方不可用都不影响另一方。
"""

import math
import re
import zlib
from array import array

# 维度取 512：256 时哈希碰撞严重（曾把英文页误判为"课时目标叙写"的高相似项）。
# 用 array('f') 存储而非 Python float 列表——7522 片 × 512 维约 15MB，可控。
DIM = 512
_WEIGHT_LOCAL = 0.45          # 向量分在混合分中的权重（其余给 BM25）

_CJK = re.compile(r"[\u4e00-\u9fff]")
_TOKEN = re.compile(r"[\u4e00-\u9fff]{1,4}|[a-zA-Z]{2,}")


def _hash(s):
    """稳定哈希：zlib.crc32 不随进程变化（内置 hash() 有随机种子，不能用）。"""
    return zlib.crc32(s.encode("utf-8", "ignore"))


def _signed_index(feat):
    """带符号哈希（signed hashing trick）。

    为什么必须带符号：不带符号时，两个不相关文本只要各自有特征**碰撞**进同一维，
    就会在该维上同向叠加，凭空产生相似度——实测教材样板页（版权/联系方式）正是
    靠这个效应长期占据"课时目标叙写"的相似榜首，而它与查询**零共同特征**。
    带符号后碰撞项正负相消，虚假相似度大幅下降。
    """
    h = _hash(feat)
    dim = h % DIM
    sign = 1 if ((h >> 16) & 1) else -1
    return dim, sign


def _features(text):
    """抽取特征：字符 bigram（抗词序/部分错字）+ 词语 unigram（抗切分歧义）。"""
    t = (text or "")[:600]                     # 限长：长文本对检索区分度贡献递减
    feats = {}
    if _CJK.search(t):
        core = "".join(_CJK.findall(t))
        for i in range(len(core) - 1):
            g = core[i:i + 2]
            feats["b:" + g] = feats.get("b:" + g, 0) + 1
    for w in _TOKEN.findall(t):
        feats["w:" + w.lower()] = feats.get("w:" + w.lower(), 0) + 1
    return feats


def vector(text):
    """文本 → 固定维度稠密向量（L2 归一，array('f') 紧凑存储）。"""
    a = array("f", [0.0]) * DIM
    for f, c in _features(text).items():
        i, sg = _signed_index(f)
        a[i] += sg * (1.0 + math.log(c))            # 带符号，抗碰撞
    norm = math.sqrt(math.fsum(x * x for x in a))
    if norm:
        a = array("f", (x / norm for x in a))
    return a


class Index:
    """极简向量索引：线性扫描 + 余弦。切片量在数千级时毫秒级，不必上 ANN。

    带 **IDF 加权**：纯 tf 向量会被教材里的样板文字（版权页、联系方式）主导——
    那些页汉字覆盖面广，和任何中文查询都有不小余弦。加入语料 IDF 后，
    通用片段权重被压低，真正有区分度的片段才会浮上来。
    """

    def __init__(self):
        self.ids, self.vecs = [], []
        self.df = array("i", [0]) * DIM
        self.n = 0

    def build(self, items):
        """items: [(id, text), ...]。两遍扫描：先统计 df，再算 tf-idf 向量。"""
        self.ids, self.vecs = [], []
        self.df = array("i", [0]) * DIM
        feats_list = []
        for i, t in items:
            f = _features(t)
            feats_list.append(f)
            self.ids.append(i)
            for k in f:
                self.df[_signed_index(k)[0]] += 1
        self.n = max(len(feats_list), 1)
        for f in feats_list:
            self.vecs.append(self._encode(f))
        return len(self.ids)

    def _idf(self, dim_i):
        """IDF（带上下截断）。

        上截断：df 极小的维度往往是碰撞噪声，不放大它的权重；
        下截断：出现过于普遍的维度（> 半数文档）区分度为零，直接置 0。
        """
        d = self.df[dim_i]
        n = self.n
        # 只有出现在「多数文档」中的维度才判定为无区分度。
        # 这里必须用 ceil 而非 int：小语料下 int(n*0.6) 会退化为 1（如 n=3 时
        # int(1.8)=1），于是只出现在 1 篇文档的特征也被误杀，向量全零，
        # 个人教师的小规模知识库会完全没有召回。ceil + 下限 2 可避免该退化。
        if d >= max(2, math.ceil(n * 0.6)):
            return 0.0                             # 过普遍，无区分度
        eff = max(d, max(50, int(n * 0.005)))      # 稀有维度按下限计，避免放大噪声
        return math.log((n + 1.0) / (eff + 1.0))

    def _encode(self, feats):
        a = array("f", [0.0]) * DIM
        for f, c in feats.items():
            i, sg = _signed_index(f)
            a[i] += sg * (1.0 + math.log(c)) * self._idf(i)
        norm = math.sqrt(math.fsum(x * x for x in a))
        if norm:
            a = array("f", (x / norm for x in a))
        return a

    def encode(self, text):
        """把查询编码为与文档同一空间的向量（复用语料 IDF）。"""
        if self.n <= 1:                      # 未建索引时退化为纯 tf 向量
            return vector(text)
        return self._encode(_features(text))

    def search(self, query, topk=5, floor=0.15):
        """余弦检索。floor：低于此相似度的结果不返回（哈希碰撞噪声）。"""
        if not self.vecs:
            return []
        q = self.encode(query)
        nz = [(i, x) for i, x in enumerate(q) if x]
        scored = []
        for idx, v in enumerate(self.vecs):
            s = 0.0
            for i, x in nz:
                s += x * v[i]
            if s >= floor:
                scored.append((s, self.ids[idx]))
        scored.sort(key=lambda x: -x[0])
        return [(round(s, 4), i) for s, i in scored[:topk]]

    def __len__(self):
        return len(self.ids)


_INDEX = Index()


def rebuild(chunks):
    """随知识库索引一起重建。chunks: [{id, text}]"""
    _INDEX.build([(c.get("id", ""), c.get("text", "")) for c in chunks])
    return len(_INDEX)


def search(query, topk=5):
    return _INDEX.search(query, topk)


def query_vector(query):
    """查询向量（与文档同一空间，含 IDF）。供 knowledge 混合打分使用。"""
    return _INDEX.encode(query)


def all_vectors():
    """供 knowledge 复用同一份向量（避免重复计算与两份索引不一致）。"""
    return list(_INDEX.vecs)


def all_ids():
    return list(_INDEX.ids)


def enabled():
    """本地向量检索是否启用（默认开；关闭时完全不参与计算，不影响 BM25）。"""
    try:
        from . import config
        cur = config.current() or {}
        return bool(cur.get("semantic_local", True))
    except Exception:
        return True


def weight():
    return _WEIGHT_LOCAL
