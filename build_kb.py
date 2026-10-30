"""知识库独立构建脚本（不依赖 Web 服务，直接跑完全部源文件切片）。

用法：
  py build_kb.py            # 增量构建（源未变化的文件复用缓存）
  py build_kb.py --fresh    # 删除缓存，全量重新切片
"""

import sys
import threading
import time

from nexedu import knowledge


def main():
    fresh = "--fresh" in sys.argv
    if fresh:
        import os
        if os.path.exists(knowledge.CACHE_PATH):
            os.remove(knowledge.CACHE_PATH)
            print("已删除旧缓存，开始全量重新切片…")

    done = threading.Event()

    def run():
        try:
            knowledge.build_all()
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()

    last = None
    while not done.wait(2):
        s = knowledge.status()
        cur = (s["done_files"], s["current"])
        if cur != last:
            print(f"[{time.strftime('%H:%M:%S')}] {s['done_files']}/{s['total_files']} "
                  f"{s['current'][:50]}", flush=True)
            last = cur

    s = knowledge.status()
    print("=" * 56)
    print(f"构建结果: {s['phase']}")
    print(f"切片总数: {s['total_chunks']}")
    print(f"说明: {s['message']}")
    failed = s.get("failed") or []
    if failed:
        print("未提取到内容的文件:")
        for f in failed:
            print(f"  - {f['file']}: {f['error']}")
    sys.exit(0 if s["phase"] == "ready" else 1)


if __name__ == "__main__":
    main()
