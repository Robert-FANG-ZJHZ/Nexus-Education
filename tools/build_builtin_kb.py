# -*- coding: utf-8 -*-
"""构建期生成「内置知识库」：把预置源目录切片 + 打标签，产出 nexedu/data/builtin_kb.json。

与旧的 chunks.json 的区别（关键）：
- chunks.json 是**运行时缓存**（带 fp 指纹、按源文件增量复用、会被重建覆盖）；
- builtin_kb.json 是**产品内置资产**——构建期一次性切片定型，随 exe 完整分发，
  运行时原样加载，不依赖桌面源目录，也不参与增量比对。

用法：py -3.12 tools/build_builtin_kb.py
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nexedu import config, knowledge  # noqa: E402

OUT = os.path.join(ROOT, "nexedu", "data", "builtin_kb.json")


def preset_dirs():
    """预置（开发者随包提供）的知识库源目录——仅构建期使用。"""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    roots = [
        os.path.join(desktop, "【山海智绘】NexEdu AI Agent", "山海智绘·知识库建设"),
        os.path.join(desktop, "山海智绘·知识库建设"),
    ]
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        subs = [os.path.join(root, s) for s in ("知识库-教科书", "知识库-课程标准",
                                                "知识库-优秀教学案例")]
        subs = [s for s in subs if os.path.isdir(s)]
        found += subs or [root]
    for p in (os.environ.get("NEXEDU_KB_PRESET") or "").split(os.pathsep):
        p = p.strip()
        if p and os.path.isdir(p):
            found.append(p)
    return found


def scan(dirs):
    files, seen = [], set()
    for d in dirs:
        for root, subdirs, fns in os.walk(d):
            subdirs[:] = [x for x in subdirs if not x.startswith((".", "~"))]
            for fn in sorted(fns):
                if fn.startswith("~$") or not fn.lower().endswith(knowledge.SUPPORTED_EXT):
                    continue
                p = os.path.join(root, fn)
                if os.path.isfile(p) and p not in seen:
                    seen.add(p)
                    files.append(p)
    return files


def main():
    dirs = preset_dirs()
    print("预置源目录：")
    for d in dirs:
        print("   %s" % d)
    if not dirs:
        raise SystemExit("未找到预置知识库目录，可用 NEXEDU_KB_PRESET 指定")

    files = scan(dirs)
    print("扫到 %d 个文件" % len(files))

    chunks, failed = [], []
    t0 = time.time()
    for i, path in enumerate(files):
        name = os.path.basename(path)
        try:
            text = knowledge._extract(path)
            made = knowledge._make_chunks(text, name, knowledge._parse_meta(path))
        except Exception as e:
            made = []
            failed.append({"file": name, "error": str(e)})
        if not made and os.path.getsize(path) > 1024:
            failed.append({"file": name, "error": "未提取到文本（损坏或扫描版）"})
        chunks += made
        if (i + 1) % 20 == 0:
            print("   %d/%d ... %d 片" % (i + 1, len(files), len(chunks)))

    data = {
        "built_at": time.time(),
        "source_dirs": dirs,
        "files": len(files),
        "chunks": chunks,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    print("内置知识库：%d 片，来自 %d 个文件，耗时 %.1fs" % (len(chunks), len(files), time.time() - t0))
    print("提取失败（属开发者侧数据问题，不进教师端提示）：%d 个" % len(failed))
    for f in failed:
        print("   - %s | %s" % (f["file"], f["error"]))
    print("输出：%s（%.1f MB）" % (os.path.relpath(OUT, ROOT),
                                  os.path.getsize(OUT) / 1048576.0))


if __name__ == "__main__":
    main()
