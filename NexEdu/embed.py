"""随包分发的本地词向量（第 2 档"本地 + 可选组件"的直接内置版）。

为什么这样做，而不是让用户下载一个嵌入模型：
  「可选组件要教师自己装」听起来很美，实际上等于没有——教师不会装。
  所以这里把它**直接训练好、随包分发**，装上就能用，体积代价约 8MB。

方法：**Random Indexing（随机索引）**，一种经典的分布式语义方法。
  1. 给词表里每个词分配一个随机的稀疏"索引向量"（若干位置 ±1，其余为 0）；
  2. 扫描语料，把中心词上下文窗口内各词的索引向量累加到中心词的向量上；
  3. 得到的累加向量就是词向量——共现越多的词，向量越接近。

选它而不是 Word2Vec / PPMI+SVD 的原因：
  - Word2Vec 需要反向传播，纯标准库实现慢且不必要；
  - PPMI+SVD 要显式构造词-词共现矩阵，4000 词规模下内存就要上百 MB，
    还要做 SVD（标准库没有），不可行；
  - Random Indexing **不需要共现矩阵**，内存只与「词表 × 维度」有关，
    纯 Python 几分钟可跑完，效果对"领域同义"足够。

诚实边界：这是**共现统计**层面的语义，能捕捉"教师-课堂-教学"这类搭配关系，
但**做不到** BERT 那种上下文相关的语义，也不含世界知识。
它的定位是"比哈希向量强一截的检索地基"，不是通用语义理解。

数据来源：内置知识库语料（已随程序分发的教材与教学资源），不引入外部语料，
因此随包分发的数据文件不涉及额外的版权或合规问题。
"""

import json
import math
import os
import random
import re
import threading
from array import array

from . import config

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_VOCAB_PATH = os.path.join(_DIR, "embed_vocab.json")
_VEC_PATH = os.path.join(_DIR, "embed_vec.f32")

DIM = 256               # 词向量维度
NONZERO = 8             # 每个词索引向量中的非零项个数
VOCAB_SIZE = 4000       # 词表规模
WINDOW = 3              # 上下文窗口（左右各取几个词）
MIN_FREQ = 6            # 进入词表的最低频次

# 近邻表的构建范围：只对高频词预计算近邻，冷僻词不值得为它花算力
NB_WORDS = 2000         # 为多少个词计算近邻（覆盖到"教学目标""课堂"这类常用词）
NB_CANDIDATES = 2000    # 近邻候选项（按频次取前 N 个词）
NB_TOPK = 6             # 每个词保留几个近邻
NB_FLOOR = 0.32         # 相似度下限，低于此值不认作近邻

# 扩展词必须过这道过滤：共现相似度高的词里混着大量虚词与高频万能词
# （"这些""更好地""然后""进行"），它们跟谁都像，放进查询只会稀释精度。
_FUNC_WORDS = {"这些", "那些", "这个", "那个", "这样", "那样", "一些", "一下",
               "然后", "进行", "通过", "没有", "就是", "可以", "什么", "自己",
               "我们", "他们", "你们", "因为", "所以", "但是", "如果", "已经",
               "还有", "之后", "之前", "对于", "关于", "以及", "中的", "其中",
               "更好地", "本课", "本书", "本单元", "全文", "学生的", "的速",
               "写的", "学者", "一人", "用水", "怎样", "应该", "如何", "这么",
               "那么", "百年", "奋斗", "我国", "主义", "社会", "发展", "提示",
               "内容", "说说", "同学们", "综合"}
_STOP_CHARS = set("的了和与在是中不为上下之其并或对把被能会要有")

_CJK = re.compile(r"[\u4e00-\u9fff]+")

_VOCAB_PATH = os.path.join(_DIR, "embed_vocab.json")   # 含词表与近邻表
_VEC_PATH = os.path.join(_DIR, "embed_vec.f32")        # 可选，仅调试/离线分析

_vocab = {}             # 词 -> id
_idf = None             # array，长度 = 词表大小
_vecs = None            # array，长度 = 词表大小 × DIM（可选加载）
_neighbors = None       # 近邻表：{词id: [[相似度, 词], ...]}
_loaded = False
_LOCK = threading.RLock()   # 并行任务下多个线程会同时首次调用 load()


# ---------------- 词表与分词 ----------------

def _ngram_freq(texts, limit=1500000):
    """统计汉字片段频次（2~4 字），用于构建词表。

    必须支持 4 字：只到 3 字时，贪心匹配会把「作者简介」切成「作者简」、
    「课程标准」切成「程标准」——这些伪词进了词表，既污染词向量，
    又让"教学目标"这类真正的四字术语无法整体命中。
    """
    freq = {}
    for t in texts:
        if len(freq) > limit:
            break
        for seg in _CJK.findall(t or ""):
            if len(seg) < 2:
                continue
            for n in (2, 3, 4):
                for i in range(len(seg) - n + 1):
                    g = seg[i:i + n]
                    freq[g] = freq.get(g, 0) + 1
    return freq


def build_vocab(texts):
    """从语料构建词表：高频优先，同频时长词优先。"""
    freq = _ngram_freq(texts)
    cands = [(w, c) for w, c in freq.items() if c >= MIN_FREQ and len(w) >= 2]
    # 同频时优先长词（"教学目标" 比 "教学" 信息量大）
    cands.sort(key=lambda kv: (-kv[1], -len(kv[0])))
    words = [w for w, _c in cands[:VOCAB_SIZE]]
    return {w: i for i, w in enumerate(words)}


def tokenize(text, vocab=None):
    """最大正向匹配分词（贪心取词表中最长的片段，4→3→2）。未命中则按单字。"""
    vocab = vocab if vocab is not None else _vocab
    out = []
    for seg in _CJK.findall(text or ""):
        i = 0
        while i < len(seg):
            hit = None
            for n in (4, 3, 2):
                if i + n <= len(seg) and seg[i:i + n] in vocab:
                    hit = seg[i:i + n]
                    break
            if hit:
                out.append(hit)
                i += len(hit)
            else:
                out.append(seg[i])
                i += 1
    return out


# ---------------- 训练 ----------------

def build(texts, seed=20240920):
    """训练词向量。返回 (词表, 向量 array, 统计信息)。

    texts: 语料（切片文本列表）。仅用内置知识库语料，不引入外部数据。
    """
    vocab = build_vocab(texts)
    n = max(len(vocab), 1)
    rnd = random.Random(seed)

    # 1) 每个词的随机索引向量：NONZERO 个位置为 ±1
    index = {}
    for w, i in vocab.items():
        idx = array("f", [0.0]) * DIM
        for _ in range(NONZERO):
            idx[rnd.randrange(DIM)] = 1.0 if rnd.random() < 0.5 else -1.0
        index[i] = idx

    # 2) 扫描语料累加：中心词 += 窗口内上下文词的索引向量（按距离衰减）
    #    累加阶段用 list 而非 array（Python 里 list 的逐元素写入明显更快），
    #    全部算完后再统一转回 array 存储。
    acc = [[0.0] * DIM for _ in range(n)]
    index = {i: list(v) for i, v in index.items()}
    df = array("i", [0]) * n           # 文档频次，用于 IDF 加权
    for t in texts:
        toks = tokenize(t, vocab)
        ids = [vocab[x] for x in toks if x in vocab]
        for w in set(ids):
            df[w] += 1
        L = len(ids)
        for i, w in enumerate(ids):
            a = acc[w]
            for d in (1, 2, 3):
                if d > WINDOW:
                    break
                wgt = 1.0 / d
                if i - d >= 0:
                    v = index[ids[i - d]]
                    for k in range(DIM):
                        a[k] += v[k] * wgt
                if i + d < L:
                    v = index[ids[i + d]]
                    for k in range(DIM):
                        a[k] += v[k] * wgt
    acc = [array("f", x) for x in acc]
    # 3) L2 归一，便于余弦比较
    for i in range(n):
        a = acc[i]
        norm = math.sqrt(math.fsum(x * x for x in a))
        if norm:
            acc[i] = array("f", (x / norm for x in a))
    idf = array("f", (math.log((len(texts) + 1.0) / (1.0 + df[i])) for i in range(n)))
    return vocab, acc, idf


def build_neighbors(vocab, vecs, idf):
    """预计算高频词的近邻表（一次性，随包分发）。

    为什么预计算、而不是查询时现算：现算意味着每次检索都要做
    「查询词数 × 词表 × 维度」次浮点运算（百万级），教师等不起。
    预计算成一张表后，运行时只是一次字典查询，成本归零。

    另一个收益是**包体积极小**：随包只需要「词表 + 近邻表」（约 0.4MB），
    完整的词向量矩阵（4000 × 256 × 4B ≈ 4MB）只在构建时用，不必进 exe。
    """
    from operator import mul
    # 按 IDF 升序取高频词：IDF 小 = 出现在更多切片里 = 更常用
    order = sorted(vocab.items(), key=lambda kv: float(idf[kv[1]]))
    def _ok(w):
        return (w not in _FUNC_WORDS and len(w) >= 2
                and not any(c in _STOP_CHARS for c in w))

    cands = [(w, i) for w, i in order[:NB_CANDIDATES] if _ok(w)]
    cand_ids = [i for _w, i in cands]
    cand_words = [w for w, _i in cands]
    word_ids = [i for w, i in order[:NB_WORDS] if _ok(w)]

    table = {}
    for w_i in word_ids:
        v = vecs[w_i]                     # vecs 是「每个词一个 array」的列表
        scored = []
        for c_i, c_w in zip(cand_ids, cand_words):
            if c_i == w_i:
                continue
            s = sum(map(mul, v, vecs[c_i]))
            if s >= NB_FLOOR:
                scored.append((s, c_w))
        if scored:
            scored.sort(key=lambda x: -x[0])
            table[str(w_i)] = [[round(s, 3), cw] for s, cw in scored[:NB_TOPK]]
    return table, order


def save(vocab, vecs, idf, neighbors=None):
    """保存随包分发的数据：词表 + 近邻表（+ 可选完整向量，仅调试用）。

    默认**不写完整向量文件**——它只在构建近邻表时需要，运行时用不到，
    写进 exe 只会白白增加约 4MB 体积。
    """
    os.makedirs(_DIR, exist_ok=True)
    with open(_VOCAB_PATH, "w", encoding="utf-8") as f:
        json.dump({"dim": DIM, "vocab": vocab,
                   "neighbors": neighbors or {}}, f, ensure_ascii=False)
    with open(os.path.join(_DIR, "embed_idf.f32"), "wb") as f:
        array("f", idf).tofile(f)
    return os.path.getsize(_VOCAB_PATH)


def save_vectors(vecs):
    """（可选）导出完整词向量，供离线分析，不随 exe 分发。"""
    flat = array("f")
    for v in vecs:
        flat.extend(v)
    p = os.path.join(config.WORKSPACE_DIR, "embed_vectors.f32")
    with open(p, "wb") as f:
        flat.tofile(f)
    return p


def load():
    """懒加载随包分发的向量；文件缺失时返回 False（调用方回退到哈希向量）。"""
    # _neighbors 必须在此声明：漏掉它的话，赋值会变成局部变量，
    # 模块级的 _neighbors 永远是 None——近邻表看着加载了，实际一个字都没生效。
    global _vocab, _vecs, _idf, _neighbors, _loaded
    # 必须持锁且二次判断：原先 `_loaded = True` 在读文件**之前**就置位，
    # 并行任务下乙线程会立刻返回，拿着还是 None 的 _vecs 去索引 → TypeError。
    with _LOCK:
        if _loaded:
            return bool(_vecs)
        try:
            with open(_VOCAB_PATH, "r", encoding="utf-8") as f:
                obj = json.load(f)
            vocab = obj.get("vocab") or {}
            neighbors = obj.get("neighbors") or {}
            n = len(vocab)
            vecs = None
            # 完整向量是可选的：随包只发词表与近邻表，运行时检索不需要它
            if os.path.exists(_VEC_PATH):
                a = array("f")
                with open(_VEC_PATH, "rb") as f:
                    a.fromfile(f, n * DIM)
                vecs = a
            try:
                b = array("f")
                with open(os.path.join(_DIR, "embed_idf.f32"), "rb") as f:
                    b.fromfile(f, n)
                idf = b
            except Exception:
                idf = array("f", [1.0]) * n
        except Exception:
            _loaded = True
            _vocab, _vecs, _idf, _neighbors = {}, None, None, None
            return False
        # 全部就绪后一次性发布，其它线程要么看到"未加载"要么看到"完整数据"
        _vocab, _vecs, _idf, _neighbors = vocab, vecs, idf, neighbors
        _loaded = True
        return True


def available():
    """随包数据是否可用。

    只看**词表与近邻表**是否加载成功——完整词向量矩阵刻意不随包（运行时用不到），
    因此不能用它是否存在来判断。
    """
    if not _loaded:
        load()
    return bool(_vocab)


def size_bytes():
    try:
        return os.path.getsize(_VOCAB_PATH) + os.path.getsize(
            os.path.join(_DIR, "embed_idf.f32"))
    except OSError:
        return 0


def stats():
    """供界面展示：是否已随包、体积、词表与近邻规模。"""
    ok = available()
    return {"ok": ok, "dim": DIM, "vocab": len(_vocab) if ok else 0,
            "neighbors": len(_neighbors or {}),
            "bytes": size_bytes(),
            "message": ("已内置本地词向量（Random Indexing，随包分发）"
                        if ok else "未内置词向量数据，使用手工同义词表")}


# ---------------- 编码与检索 ----------------

def encode(text):
    """文本 → 句向量：词向量按 IDF 加权平均后 L2 归一。"""
    if not available() or _vecs is None:
        # 随包只分发词表与近邻表（完整向量矩阵刻意不打包），此时句向量不可用，
        # 调用方回退到哈希向量（vectors 模块）——不能硬取 _vecs，否则 TypeError。
        return None
    toks = tokenize(text)
    if not toks:
        return None
    out = array("f", [0.0]) * DIM
    wsum = 0.0
    for w in toks:
        i = _vocab.get(w)
        if i is None:
            continue
        wgt = float(_idf[i]) if _idf is not None and i < len(_idf) else 1.0
        base = i * DIM
        for k in range(DIM):
            out[k] += _vecs[base + k] * wgt
        wsum += wgt
    if wsum <= 0:
        return None
    out = array("f", (x / wsum for x in out))
    norm = math.sqrt(math.fsum(x * x for x in out))
    if norm:
        out = array("f", (x / norm for x in out))
    return out


def cosine(a, b):
    if not a or not b:
        return 0.0
    nz = [(i, x) for i, x in enumerate(a) if x]
    s = 0.0
    for i, x in nz:
        s += x * b[i]
    return s


def similar(word, topk=8):
    """查与某个词最相近的词（自检用）。优先查预计算表，表内没有再现算。"""
    if not available():
        return []
    i = _vocab.get(word)
    if i is None:
        return []
    hit = (_neighbors or {}).get(str(i))
    if hit:
        return [(s, w) for s, w in hit[:topk]]
    if _vecs is None:
        return []
    v = _vecs[i * DIM:(i + 1) * DIM]
    out = []
    for w, j in _vocab.items():
        if j == i:
            continue
        s = cosine(v, _vecs[j * DIM:(j + 1) * DIM])
        if s > 0.2:
            out.append((round(s, 3), w))
    out.sort(key=lambda x: -x[0])
    return out[:topk]


def expand(text, per_word=2, max_extra=4):
    """用随包分发的近邻表做**语义查询扩展**。

    与手工同义词表的区别：同义词表只能覆盖我事先想到的说法，
    近邻表是从教材语料的共现里学出来的——它能给出"评价 → 反思、改进、总结"
    这类我没写进词表、但在教学语境里确实相关的扩展词。

    返回追加的扩展词列表（不含原查询词）。表不可用时返回空列表，
    调用方的手工同义词表照常生效，因此功能不会退化。
    """
    if not available() or not _neighbors:
        return []
    extra = []
    for w in tokenize(text):
        i = _vocab.get(w)
        if i is None:
            continue
        for _s, nb in (_neighbors.get(str(i)) or [])[:per_word]:
            if nb in text or nb in extra or nb in _FUNC_WORDS:
                continue
            if any(c in _STOP_CHARS for c in nb):
                continue
            extra.append(nb)
            if len(extra) >= max_extra:
                return extra
    return extra
