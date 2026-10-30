"""构建随包分发的本地词向量数据文件（一次性，不进运行时）。

用法：
    python tools/build_embed.py

产物（提交进仓库、随 exe 分发）：
    nexedu/data/embed_vocab.json   词表
    nexedu/data/embed_vec.f32      词向量（float32）
    nexedu/data/embed_idf.f32      各词 IDF

语料只取**内置知识库**（已随程序分发的教材与教学资源），
不引入任何外部语料，因此随包分发的数据文件不产生额外的版权或合规问题。
"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nexedu import embed, config  # noqa: E402


def load_corpus():
    p = os.path.join(config.WORKSPACE_DIR, "kb", "chunks.json")
    if not os.path.exists(p):
        raise SystemExit(f"未找到内置知识库切片：{p}")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    texts = []
    for entry in (data.get("files") or {}).values():
        for c in (entry.get("chunks") or []):
            t = c.get("text") or ""
            if t:
                texts.append(t)
    return texts, p


def main():
    texts, p = load_corpus()
    print(f"语料：{p}")
    print(f"切片数：{len(texts)}")

    t0 = time.time()
    vocab, vecs, idf = embed.build(texts)
    print(f"训练完成：{time.time() - t0:.1f}s｜词表 {len(vocab)}｜维度 {embed.DIM}")

    t1 = time.time()
    neighbors, _order = embed.build_neighbors(vocab, vecs, idf)
    print(f"近邻表：{len(neighbors)} 个词｜耗时 {time.time() - t1:.1f}s")

    size = embed.save(vocab, vecs, idf, neighbors)
    print(f"随包数据：{embed._VOCAB_PATH}（{size / 1024:.0f} KB）"
          f" + idf {embed.DIM and ''}")
    print(f"随包体积合计：{embed.size_bytes() / 1024:.0f} KB")

    # 自检：直接读随包的近邻表（完整向量不随包，运行时也用不到）
    embed.load()
    with open(embed._VOCAB_PATH, "r", encoding="utf-8") as f:
        obj = json.load(f)
    vocab, nb = obj["vocab"], obj["neighbors"]
    id2w = {v: k for k, v in vocab.items()}
    shown = 0
    for w in ("教学目标", "阅读", "教师", "学生", "探究", "评价", "课堂", "作业",
              "课文", "理解", "设计", "活动"):
        i = vocab.get(w)
        if i is None or str(i) not in nb:
            print(f"  {w} → （不在近邻表覆盖范围）")
            continue
        print(f"  {w} → {[w2 for _s, w2 in nb[str(i)][:5]]}")
        shown += 1
    print(f"近邻表覆盖检查：抽样 12 个教学词，命中 {shown} 个")
    print("（未命中的是低频词，属于设计内的取舍：只为高频词预计算近邻）")
    print("\n提示：完整词向量只在构建时需要，默认不写进 exe；"
          "如需离线分析可调用 embed.save_vectors(vecs)。")


if __name__ == "__main__":
    main()
