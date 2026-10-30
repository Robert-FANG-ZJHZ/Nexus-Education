"""快速语法/导入检查：编译所有源文件并导入关键模块。"""
import os
import py_compile
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

bad = 0
pkg = os.path.join(ROOT, "nexedu")
for fn in sorted(os.listdir(pkg)):
    if not fn.endswith(".py"):
        continue
    p = os.path.join(pkg, fn)
    try:
        py_compile.compile(p, doraise=True)
    except Exception as e:
        bad += 1
        print("COMPILE FAIL", fn, e)

for mod in ("tools", "mcp", "jobs", "triggers", "package", "workflow", "agents"):
    try:
        __import__("nexedu." + mod)
        print("import ok  nexedu.%s" % mod)
    except Exception as e:
        bad += 1
        print("IMPORT FAIL nexedu.%s" % mod)
        traceback.print_exc()

try:
    import nexedu.server as srv
    print("import ok  nexedu.server")
except Exception:
    bad += 1
    print("IMPORT FAIL nexedu.server")
    traceback.print_exc()

from nexedu import tools
print("tools:", sorted(tools.TOOLS))
print("BAD =", bad)
sys.exit(1 if bad else 0)
