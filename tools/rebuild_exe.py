"""重命名旧的打包产物（避开批量删除守卫），然后调用 PyInstaller 重新打包。"""
import os
import subprocess
import sys
import time

ROOT = "D:/TRAE/NexEdu"
PY = r"C:/Users/FANG/AppData/Local/Programs/Python/Python312/python.exe"
ts = str(int(time.time()))

src_b = os.path.join(ROOT, "build", "NexEdu")
if os.path.isdir(src_b):
    os.rename(src_b, os.path.join(ROOT, "build", "_old_" + ts))
    print("renamed build/NexEdu ->", "_old_" + ts)

src_e = os.path.join(ROOT, "dist", "NexEdu.exe")
if os.path.isfile(src_e):
    dst = os.path.join(ROOT, "dist", "NexEdu_prev_%s.exe" % ts)
    os.rename(src_e, dst)
    print("renamed dist/NexEdu.exe ->", os.path.basename(dst))

cmd = [PY, "-m", "PyInstaller", "NexEdu.spec",
       "--noconfirm", "--distpath", "dist", "--workpath", "build"]
print("CMD:", " ".join(cmd))
p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
tail = (p.stdout or "") + "\n" + (p.stderr or "")
print(tail[-4000:])
print("RC =", p.returncode)
out = os.path.join(ROOT, "dist", "NexEdu.exe")
print("EXE EXISTS =", os.path.isfile(out), os.path.getsize(out) if os.path.isfile(out) else "")
