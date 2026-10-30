# -*- coding: utf-8 -*-
"""构建期资源预加密 + 大数据模块"数据化"瘦身（P2 + 方案B）。

用法（打包前运行，一般由 tools/build_secure.py 调用）：
    py -3.12 tools/pack_assets.py

产出：
  1. nexedu/data/assets.pak
     - web/index.html、知识库种子：明文不再随包分发；
     - 专家矩阵 / 内置技能的数据：从 .py 移出后加密存于此。
  2. build/obf_pkg/nexedu/{experts_data,skilllib}.py —— "瘦版"模块
     只留加载器（几 KB），数据从 pak 解密。源码 nexedu/ 下仍是完整版
     （开发用），打包用瘦版，于是这两个文件：
       · 明文提示词不再出现在 pyc / exe 中；
       · 体积降到 PyArmor trial 的 big-script 阈值以下，可被加密。

pak 格式：
    b"NEXEDU-PAK1" + 重复[ u16 名称长度 | 名称 | u32 密文长度 | 密文 ]
每条密文 = AES-256-CTR 密文 + HMAC-SHA256（见 nexedu/_crypto.encrypt）。
"""

import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nexedu import _crypto, assets  # noqa: E402

OUT = os.path.join(ROOT, "nexedu", "data", assets.PAK_NAME)
OBF_DIR = os.path.join(ROOT, "build", "obf_pkg", "nexedu")

# (pak 内条目名, 源文件)：随包分发、且需要防明文读取的核心资产。
# embed_vocab / embed_idf / curriculum 是模型与课标数据，非提示词原文，且
# embed 在模块导入期就要读，走 pak 会把解密开销压到启动路径上——故不纳入。
ITEMS = [
    ("web/index.html", os.path.join(ROOT, "web", "index.html")),
    # 内置知识库（构建期切片定型的产品资产，非运行时缓存）
    ("kb/builtin_kb.json", os.path.join(ROOT, "nexedu", "data", "builtin_kb.json")),
]

# 需要"数据化瘦身"的模块：顶层超大列表字面量会被移出 .py
SLIM_MODULES = ["experts_data.py", "skilllib.py"]

_MIN_SLIM_BYTES = 8000      # 小于此值的顶层列表不做数据化


def _slim(src_path, out_path, pak_prefix):
    """把一个含大数据的模块改写为瘦版加载器，返回 (pak条目, 描述)。"""
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    lines = src.splitlines()
    tree = ast.parse(src)

    targets = []
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.List)):
            continue
        seg = "\n".join(lines[node.lineno - 1:node.end_lineno])
        if len(seg.encode("utf-8")) < _MIN_SLIM_BYTES:
            continue
        try:
            data = ast.literal_eval(node.value)
        except Exception:
            continue
        targets.append((node.targets[0].id, node.lineno, node.end_lineno, data))

    if not targets:
        return [], []

    entries, repl = [], {}
    for name, lineno, end_lineno, data in targets:
        key = "%s/%s.json" % (pak_prefix, name.lower())
        entries.append((key, json.dumps(data, ensure_ascii=False).encode("utf-8")))
        repl[lineno] = (end_lineno, name, key)

    head = [
        "# -*- coding: utf-8 -*-",
        '"""构建期数据化瘦版：大数据已移出本文件，改由加密资源包 assets.pak 提供。',
        "",
        "源码版（nexedu/%s）仍含完整数据供开发使用；打包时用本瘦版，" % os.path.basename(src_path),
        "避免专家提示词 / 内置技能以明文出现在 pyc 与 exe 中。",
        '"""',
        "import json",
        "",
        "from . import assets as _assets",
        "",
        "",
        "def _pak(name):",
        "    raw = _assets.read_data(name)",
        "    if raw is None:",
        '        raise RuntimeError("加密资源缺失: " + name)',
        "    return raw",
        "",
        "",
    ]

    body, i = [], 1
    while i <= len(lines):
        if i in repl:
            end_lineno, name, key = repl[i]
            body.append('%s = json.loads(_pak("%s").decode("utf-8"))' % (name, key))
            i = end_lineno + 1
        else:
            body.append(lines[i - 1])
            i += 1

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(head + body) + "\n")
    # 注意：key 必须就地重算，不能引用上面循环里的 key（否则会取到最后一个值）
    return entries, [(name, "%s/%s.json" % (pak_prefix, name.lower()),
                      len(json.dumps(data, ensure_ascii=False)))
                     for name, _, _, data in targets]


def slim_all():
    """生成全部瘦版模块，返回 [(pak条目名, 数据字节)]。"""
    os.makedirs(OBF_DIR, exist_ok=True)
    entries = []
    for fname in SLIM_MODULES:
        src_path = os.path.join(ROOT, "nexedu", fname)
        out_path = os.path.join(OBF_DIR, fname)
        ents, desc = _slim(src_path, out_path, "nexedu/data/slim")
        for name, key, n in desc:
            print("  slim %-16s %-22s %7d bytes -> %s" % (fname, name, n, key))
        entries += ents
    return entries


# 超过此体积用"快速模式"（zlib + 大整数 XOR）：纯 Python AES 加密 12MB 的知识库
# 在启动时要 96 秒，不可接受；快速模式约 1 秒，且压缩后体积更小。
FAST_MIN_BYTES = 512 * 1024


def _enc(k, mk, raw):
    if len(raw) >= FAST_MIN_BYTES:
        return _crypto.fast_encrypt(mk, raw)
    return _crypto.encrypt(k, mk, raw)


def _dec(k, mk, blob):
    if blob[:4] == _crypto._FAST_MAGIC:
        return _crypto.fast_decrypt(mk, blob)
    return _crypto.decrypt(k, mk, blob)


def build(extra=()):
    k, mk = assets.pak_keys()
    buf = bytearray(assets._MAGIC)
    packed = []
    for name, src in ITEMS:
        if not os.path.exists(src):
            print("  skip (missing): %s" % name)
            continue
        with open(src, "rb") as f:
            packed.append((name, f.read()))
    packed += list(extra)
    for name, raw in packed:
        ct = _enc(k, mk, raw)
        nb = name.encode("utf-8")
        buf += len(nb).to_bytes(2, "big") + nb
        buf += len(ct).to_bytes(4, "big") + ct
        print("  add  %-34s %8d -> %8d" % (name, len(raw), len(ct)))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(bytes(buf))


def verify():
    """回读校验：每条都能解密且长度一致。"""
    k, mk = assets.pak_keys()
    with open(OUT, "rb") as f:
        raw = f.read()
    if not raw.startswith(assets._MAGIC):
        raise SystemExit("pak magic 错误")
    pos, ok, total = len(assets._MAGIC), 0, 0
    while pos + 6 <= len(raw):
        nlen = int.from_bytes(raw[pos:pos + 2], "big")
        pos += 2
        name = raw[pos:pos + nlen].decode("utf-8")
        pos += nlen
        dlen = int.from_bytes(raw[pos:pos + 4], "big")
        pos += 4
        plain = _dec(k, mk, raw[pos:pos + dlen])
        pos += dlen
        total += len(plain)
        ok += 1
        print("  ok   %-34s %8d bytes" % (name, len(plain)))
    if pos != len(raw):
        raise SystemExit("pak 尾部长度不一致")
    print("  共 %d 条，明文合计 %.1f KB" % (ok, total / 1024.0))


def main():
    print("1) 大数据模块瘦身 -> %s" % os.path.relpath(OBF_DIR, ROOT))
    extra = slim_all()
    print("2) 打包资源 -> %s" % os.path.relpath(OUT, ROOT))
    build(extra)
    print("3) 校验：")
    verify()
    print("完成。pak 体积 %.1f KB" % (os.path.getsize(OUT) / 1024.0))


if __name__ == "__main__":
    main()
