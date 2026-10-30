# -*- coding: utf-8 -*-
"""一键加固构建：数据化瘦身 → 资源加密 → PyArmor 加密 → 组装 → PyInstaller。

用法：
    py -3.12 tools/build_secure.py            # 全流程（含打包）
    py -3.12 tools/build_secure.py --no-exe   # 只做加固与组装，不跑 PyInstaller

为什么需要它：手动分步很容易漏（尤其是"PyArmor 输出在根目录要移回包内"、
"必须用主脚本目录承载加密包"这两个坑），固化成脚本才可靠。

产物：
    build/obf_pkg/    PyArmor 加密后的包（中间产物）
    build/pkg/        供 PyInstaller 使用的入口目录（main.py + 加密包）
    dist/NexEdu.exe   最终单文件程序

诚实说明（2026-10-06 补）：PyArmor 若因许可不可用（trial out of license）
或未安装而跳过，最终 exe **没有字节码加密**——提示词与资源密钥可被
PyInstaller 解包提取。此时脚本会明确告警，绝不静默产出"看起来加固了"的包。
"""

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
OBF = os.path.join(ROOT, "build", "obf_pkg")
PKG = os.path.join(ROOT, "build", "pkg")

# 需要 PyArmor 加密的模块
MODULES = ["_crypto.py", "assets.py", "guard.py", "workflow.py",
           "experts_data.py", "skilllib.py"]

_obfuscated_ok = False


def _pyarmor_exe():
    """定位 pyarmor.exe（它装到 Scripts 目录，可能不在 PATH）。"""
    found = shutil.which("pyarmor")
    if found:
        return found
    cand = os.path.join(os.path.dirname(PY), "Scripts", "pyarmor.exe")
    return cand if os.path.exists(cand) else ""


def _run(cmd, cwd=ROOT):
    def show(c):
        if not os.path.isabs(c):
            return c
        try:
            return os.path.relpath(c, ROOT)
        except ValueError:      # 跨盘符（如 pyarmor.exe 在 C:、项目在 D:）
            return c
    print("\n$ %s" % " ".join(show(c) for c in cmd))
    r = subprocess.run(cmd, cwd=cwd)
    if r.returncode != 0:
        raise SystemExit("步骤失败：%s" % cmd[-1])
    return r


def clean():
    for d in (OBF, PKG):
        if os.path.isdir(d):
            shutil.rmtree(d)
            print("clean %s" % os.path.relpath(d, ROOT))


def seed_package():
    """把源码包复制为加密基底（随后会被瘦版 / PyArmor 产物覆盖）。

    dirs_exist_ok：允许在不清空旧目录的情况下覆盖重建（--no-clean 时用），
    避免为了"干净"而整目录删除——构建中间目录不该走破坏性删除。
    """
    src = os.path.join(ROOT, "nexedu")
    dst = os.path.join(OBF, "nexedu")
    shutil.copytree(src, dst, dirs_exist_ok=True)
    print("seed %d 个文件 -> %s" % (len(os.listdir(dst)), os.path.relpath(dst, ROOT)))


def build_kb():
    """生成内置知识库（构建期切片定型）。已存在则跳过，--rebuild-kb 强制重建。"""
    out = os.path.join(ROOT, "nexedu", "data", "builtin_kb.json")
    if os.path.exists(out) and "--rebuild-kb" not in sys.argv:
        print("内置知识库已存在，跳过生成（内容更新时加 --rebuild-kb）")
        return
    _run([PY, os.path.join(ROOT, "tools", "build_builtin_kb.py")])


def pack():
    _run([PY, os.path.join(ROOT, "tools", "pack_assets.py")])


def obfuscate():
    global _obfuscated_ok
    exe = _pyarmor_exe()
    if not exe:
        print("[WARN] 未找到 pyarmor，跳过字节码加密——最终 exe 未做字节码加密，"
              "提示词与资源密钥可被 PyInstaller 解包提取。")
        return
    srcs = [os.path.join(OBF, "nexedu", m) for m in MODULES]
    srcs = [s for s in srcs if os.path.exists(s)]
    try:
        r = subprocess.run([exe, "gen", "-O", OBF] + srcs, cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
    except OSError as e:
        print("[WARN] pyarmor 调用失败，跳过字节码加密：%s" % e)
        return
    tail = ((r.stdout or "") + "\n" + (r.stderr or ""))[-2000:]
    if r.returncode != 0:
        if "out of license" in tail:
            print("[WARN] PyArmor 许可不可用（out of license）：本次 exe **未做字节码加密**。"
                  "\n       提示词与资源密钥可被 PyInstaller 解包提取——"
                  "如需真实加固，请购买 PyArmor 许可或改用 Nuitka。")
        else:
            print("[WARN] pyarmor 退出码 %d，跳过字节码加密：\n%s"
                  % (r.returncode, tail[-800:]))
        return
    # PyArmor 按 basename 写到 -O 根目录，需移回包内覆盖
    moved = 0
    for m in MODULES:
        p = os.path.join(OBF, m)
        if os.path.exists(p):
            shutil.move(p, os.path.join(OBF, "nexedu", m))
            print("  move %s -> nexedu/" % m)
            moved += 1
    _obfuscated_ok = moved > 0
    if not _obfuscated_ok:
        print("[WARN] pyarmor 未产出任何加密文件——最终 exe 未做字节码加密。")


def assemble():
    os.makedirs(PKG, exist_ok=True)
    shutil.copy(os.path.join(ROOT, "main.py"), os.path.join(PKG, "main.py"))
    shutil.copytree(os.path.join(OBF, "nexedu"), os.path.join(PKG, "nexedu"),
                    dirs_exist_ok=True)
    for name in os.listdir(OBF):
        if name.startswith("pyarmor_runtime"):
            shutil.copytree(os.path.join(OBF, name), os.path.join(PKG, name),
                            dirs_exist_ok=True)
            print("  runtime %s" % name)
    print("assemble -> %s" % os.path.relpath(PKG, ROOT))


def _retire_old_exe():
    """把上一版 exe 挪开，而不是删掉。

    PyInstaller 在写新产物前会 os.remove 旧 exe，这一步会被环境的删除保护拦截
    （os.remove 本身直接抛错），导致整轮构建失败；所以先由我们改名归档。
    """
    exe = os.path.join(ROOT, "dist", "NexEdu.exe")
    if not os.path.isfile(exe):
        return
    old = os.path.join(ROOT, "dist", "NexEdu_prev_%s.exe" % time.strftime("%Y%m%d%H%M%S"))
    try:
        os.replace(exe, old)
        print("旧产物已归档（未删除）：%s" % os.path.basename(old))
    except OSError as e:
        print("警告：无法挪开旧 exe（%s），PyInstaller 可能会因无法覆写而失败" % e)


def build_exe():
    _retire_old_exe()
    _run([PY, "-m", "PyInstaller", os.path.join(ROOT, "NexEdu.spec"), "--noconfirm"])


def main():
    build = "--no-exe" not in sys.argv
    if "--no-clean" not in sys.argv:
        clean()
    else:
        print("skip clean（--no-clean：保留并覆盖旧构建目录）")
    seed_package()
    build_kb()      # 内置知识库切片定型（产物供 pack 加密）
    pack()          # 瘦版 + assets.pak（瘦版必须早于 PyArmor）
    obfuscate()
    assemble()
    if build:
        build_exe()
        print("\n完成：dist/NexEdu.exe"
              + ("（字节码加密：%s）" % ("已生效" if _obfuscated_ok else "未生效")))
    else:
        print("\n已组装，跳过打包。运行：py -3.12 -m PyInstaller NexEdu.spec --noconfirm")


if __name__ == "__main__":
    main()
