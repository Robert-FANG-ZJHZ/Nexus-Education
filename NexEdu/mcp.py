"""MCP 客户端（Model Context Protocol，零第三方依赖）。

MCP 是智能体时代的「USB-C 接口」：任何实现了 MCP 的外部服务（本地文件系统、
数据库、学校内网系统、第三方教学工具…）都能把工具暴露给智能体调用。
本模块用标准库实现一个 **stdio 传输** 的 MCP 客户端：

- 以子进程启动 MCP Server，走 **JSON-RPC 2.0 over stdio**；
- ``initialize`` → ``tools/list`` → ``tools/call`` 三步握手；
- 把远端工具**注册进本地工具注册表**（``tools.register``），
  从此专家调用外部工具与调用本地工具完全一样——**工具即插即用**；
- 连接失败/超时不影响主程序：降级为「该工具不可用」，并记录状态可查。

同时支持两种常见的 stdio 分帧：
  1. 换行分隔的 JSON（MCP 现行标准）；
  2. ``Content-Length: N\r\n\r\n{json}``（LSP 风格，部分实现仍在用）。

配置来自 ``workspace/mcp.json``：
    {"servers": [{"name":"files","command":"python","args":["-m","xxx"],
                  "env":{}, "enabled":true}]}
"""

import json
import os
import queue
import subprocess
import threading
import time

from . import config

CONFIG_PATH = os.path.join(config.WORKSPACE_DIR, "mcp.json")

DEFAULT_TIMEOUT = 20        # 单次请求超时（秒）
START_TIMEOUT = 15          # initialize 握手超时（秒）

# 注册表前缀：避免与内置工具重名，也便于一眼看出来自哪个 MCP 服务
PREFIX = "mcp_"


def _safe(name):
    """工具名规整：只保留字母数字与下划线。"""
    out = "".join(ch if (ch.isalnum() or ch == "_") else "_"
                  for ch in (name or ""))
    return out.strip("_").lower()


class MCPClient:
    """单个 MCP Server 的 stdio 客户端。"""

    def __init__(self, name, command, args=None, env=None, cwd=None):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd or None
        self.proc = None
        self.tools = []             # [{"name","description","inputSchema"}]
        self.error = ""
        self.connected = False
        self._id = 0
        self._lock = threading.RLock()
        self.server_info = {}
        # 读取线程 + 队列：Windows 上 select 不支持管道，统一用后台线程收帧
        self._queue = queue.Queue()
        self._reader = None

    # ---- 生命周期 ----
    def start(self):
        if self.proc is not None:
            return True
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in self.env.items()})
        flags = 0
        if os.name == "nt":     # 不弹控制台窗口
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            self.proc = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=env, cwd=self.cwd,
                creationflags=flags)
        except Exception as e:
            self.error = "启动失败：%s" % e
            return False
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name="mcp-" + self.name)
        self._reader.start()
        try:
            self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "NexEdu", "version": "1.0"},
            }, timeout=START_TIMEOUT)
            # 通知：已初始化（无 id，不需要响应）
            self._notify("notifications/initialized", {})
            lst = self._request("tools/list", {}, timeout=START_TIMEOUT)
            items = ((lst or {}).get("result") or {}).get("tools") or []
            self.tools = [{"name": t.get("name") or "",
                           "description": t.get("description") or "",
                           "inputSchema": t.get("inputSchema") or {}}
                          for t in items if t.get("name")]
            self.connected = True
            return True
        except Exception as e:
            self.error = "握手失败：%s" % e
            self.close()
            return False

    def close(self):
        p, self.proc = self.proc, None
        self.connected = False
        if p is None:
            return
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    # ---- JSON-RPC ----
    def _next_id(self):
        self._id += 1
        return self._id

    def _write(self, obj):
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def _read_loop(self):
        """后台收帧：兼容换行分隔 JSON 与 Content-Length 两种分帧。"""
        f = self.proc.stdout if self.proc else None
        if f is None:
            return
        try:
            while True:
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                if line.lower().startswith(b"content-length:"):
                    n = int(line.split(b":", 1)[1].strip() or 0)
                    while True:                 # 读到空行（头部结束）
                        h = f.readline()
                        if not h or not h.strip():
                            break
                        if h.lower().startswith(b"content-length:"):
                            n = int(h.split(b":", 1)[1].strip() or 0)
                    body = f.read(n) if n > 0 else b""
                    try:
                        self._queue.put(json.loads(body.decode("utf-8")))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pass
                    continue
                try:
                    self._queue.put(json.loads(line.decode("utf-8")))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
        except Exception:
            pass
        finally:
            self._queue.put({"__eof__": True})

    def _read_message(self, timeout):
        """取一条服务端消息（服务端通知会被调用方按 id 过滤掉）。"""
        try:
            msg = self._queue.get(timeout=timeout)
        except queue.Empty:
            if self.proc is None or self.proc.poll() is not None:
                raise IOError("MCP 服务已退出")
            raise TimeoutError("MCP 响应超时（%ss）" % timeout)
        if isinstance(msg, dict) and msg.get("__eof__"):
            raise IOError("MCP 服务已断开")
        return msg

    def _request(self, method, params=None, timeout=DEFAULT_TIMEOUT):
        if self.proc is None:
            raise IOError("MCP 服务未启动")
        mid = self._next_id()
        with self._lock:
            try:
                self._write({"jsonrpc": "2.0", "id": mid, "method": method,
                             "params": params or {}})
            except (BrokenPipeError, OSError) as e:
                raise IOError("写入 MCP 服务失败：%s" % e)
            while True:
                msg = self._read_message(timeout)
                if msg.get("id") != mid:
                    continue            # 服务端通知，跳过
                if "error" in msg:
                    raise IOError(str(msg["error"]))
                return msg

    def _notify(self, method, params=None):
        try:
            self._write({"jsonrpc": "2.0", "method": method,
                         "params": params or {}})
        except Exception:
            pass

    def call_tool(self, name, arguments=None, timeout=DEFAULT_TIMEOUT):
        """调用远端工具，返回纯文本结果（MCP content 数组拼成的文本）。"""
        res = self._request("tools/call",
                            {"name": name, "arguments": arguments or {}},
                            timeout=timeout)
        content = ((res or {}).get("result") or {}).get("content") or []
        parts = []
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text":
                parts.append(c.get("text") or "")
            else:
                parts.append(json.dumps(c, ensure_ascii=False)[:500])
        if not parts:
            return "(MCP 工具 %s 无文本返回)" % name
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# 配置与服务池
# ---------------------------------------------------------------------------
class MCPPool:
    """管理所有已配置的 MCP 服务与它们注册进来的工具。"""

    def __init__(self):
        self.clients = {}       # name -> MCPClient
        self.registered = {}    # 本地工具名 -> (server, remote 名)
        self.last_error = ""
        self.synced_at = 0

    # ---- 配置 ----
    def load_config(self):
        if not os.path.exists(CONFIG_PATH):
            return {"servers": []}
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.last_error = "配置文件读取失败：%s" % e
            return {"servers": []}
        return d if isinstance(d, dict) else {"servers": []}

    def save_config(self, data):
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, CONFIG_PATH)
            return True
        except OSError as e:
            self.last_error = "配置写入失败：%s" % e
            return False

    # ---- 同步 ----
    def sync(self, force=False):
        """按配置连接所有启用中的服务，并把它们的工具注册进本地注册表。"""
        from . import tools as toolset
        cfg = self.load_config()
        wanted = {}
        for s in (cfg.get("servers") or []):
            if not s.get("enabled", True):
                continue
            name = _safe(s.get("name") or "")
            if not name or not s.get("command"):
                continue
            wanted[name] = s

        # 断开已移除/已禁用的服务
        for name in list(self.clients):
            if name not in wanted:
                self._unregister_server(name)
                self.clients.pop(name, None).close()

        for name, s in wanted.items():
            old = self.clients.get(name)
            if old is not None and old.connected and not force:
                continue
            if old is not None:
                self._unregister_server(name)
                old.close()
            c = MCPClient(name, s.get("command"), s.get("args") or [],
                          s.get("env") or {}, s.get("cwd") or None)
            ok = c.start()
            self.clients[name] = c
            if ok:
                self._register_server(c)
            else:
                self.last_error = "%s：%s" % (name, c.error)
        self.synced_at = time.time()
        return self.status()

    # ---- 注册 ----
    def _register_server(self, client):
        from . import tools as toolset

        def _make(server_name, remote_name):
            def _call(**kw):
                c = self.clients.get(server_name)
                if c is None or not c.connected:
                    return "(MCP 服务 %s 未连接，工具 %s 暂不可用)" % (
                        server_name, remote_name)
                try:
                    return c.call_tool(remote_name, kw)
                except Exception as e:
                    return "(MCP 工具 %s 调用失败：%s)" % (remote_name, e)
            return _call

        for t in client.tools:
            remote = t["name"]
            local = PREFIX + _safe(client.name) + "_" + _safe(remote)
            schema = t.get("inputSchema") or {}
            props = schema.get("properties") or {}
            if not isinstance(props, dict):
                props = {}
            arg_desc = {}
            for k, v in props.items():
                v = v if isinstance(v, dict) else {}
                arg_desc[k] = v.get("description") or v.get("type") or ""
            if not arg_desc:                      # 无参工具也要能被识别
                arg_desc = {}
            desc = "[MCP:%s] %s" % (client.name, t.get("description") or remote)
            toolset.register(local, _make(client.name, remote), desc,
                             arg_desc,
                             required=list(schema.get("required") or []),
                             side_effect=False, retry=False)
            self.registered[local] = (client.name, remote)

    def _unregister_server(self, name):
        from . import tools as toolset
        for local, (srv, _remote) in list(self.registered.items()):
            if srv == name:
                toolset.unregister(local)
                self.registered.pop(local, None)

    # ---- 状态 ----
    def status(self):
        return {
            "config_path": CONFIG_PATH,
            "servers": [{
                "name": n,
                "connected": c.connected,
                "error": c.error,
                "tools": [t["name"] for t in c.tools],
                "registered": [k for k, v in self.registered.items() if v[0] == n],
            } for n, c in self.clients.items()],
            "tool_count": len(self.registered),
            "last_error": self.last_error,
            "synced_at": self.synced_at,
        }

    def shutdown(self):
        for c in self.clients.values():
            c.close()
        self.clients.clear()
        self.registered.clear()


POOL = MCPPool()


def auto_start():
    """进程启动时按需自动连接（无配置则什么都不做）。"""
    if (os.environ.get("NEXEDU_MCP_AUTOSTART") or "1").strip() in ("0", "false"):
        return None
    try:
        cfg = POOL.load_config()
        if not (cfg.get("servers") or []):
            return None
        return POOL.sync()
    except Exception as e:
        POOL.last_error = "自动连接失败：%s" % e
        return None
