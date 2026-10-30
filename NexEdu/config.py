"""配置读取与持久化（零第三方依赖）。

配置优先级：settings.json（界面保存，可热更新） > .env（模板） > 内置默认。
"""

import json
import logging
import os
import sys

_log = logging.getLogger("nexedu.config")


def _is_frozen():
    return bool(getattr(sys, "frozen", False))


def _resource_dir():
    """只读资源根目录：源码运行=项目根；打包后=PyInstaller 解包目录(_MEIPASS)。"""
    if _is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app_dir():
    """可写数据根目录：源码运行=项目根；打包后=exe 所在目录。"""
    if _is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE_DIR = _app_dir()
WEB_DIR = os.path.join(_resource_dir(), "web")


def _probe_writable(d):
    """探测目录是否可写（创建探针文件，成功即删）。"""
    try:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, ".nexedu_write_probe")
        with open(p, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(p)
        return True
    except OSError:
        return False


def _default_workspace_dir():
    return os.path.join(_app_dir(), "workspace")


def _workspace_dir():
    """可写数据工作区目录。

    本地部署常见坑：exe 被放进 C:\\Program Files 或只读共享盘后，
    exe 同级目录不可写，会话/教案/知识库全部写失败。这里在启动时探测：
    1. 环境变量 NEXEDU_WORKSPACE 显式指定 → 优先使用；
    2. 默认 exe/项目同级 workspace 可写 → 沿用（便携分发不搬家）；
    3. 不可写 → 迁移到 %LOCALAPPDATA%\\NexEdu\\workspace（用户目录必可写），
       并尽力把旧工作区内容复制过去（保留教师已有数据）。
    """
    env = os.environ.get("NEXEDU_WORKSPACE", "").strip()
    if env:
        return env
    default = _default_workspace_dir()
    if _probe_writable(default):
        return default
    fallback = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "NexEdu", "workspace")
    _log.warning("程序目录不可写（%s），工作区迁移到 %s", default, fallback)
    try:
        os.makedirs(fallback, exist_ok=True)
        # 尽力迁移旧数据（失败不影响启动）
        if os.path.isdir(default):
            import shutil
            for fn in os.listdir(default):
                src = os.path.join(default, fn)
                dst = os.path.join(fallback, fn)
                try:
                    if os.path.isdir(src):
                        shutil.copytree(src, dst, dirs_exist_ok=True)
                    elif os.path.isfile(src) and not os.path.exists(dst):
                        shutil.copy2(src, dst)
                except OSError:
                    pass
    except OSError:
        return default
    return fallback


WORKSPACE_DIR = _workspace_dir()
SETTINGS_PATH = os.path.join(_app_dir(), "settings.json")

# 默认端点与模型（智谱 GLM）
DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-5.3"

# 模型预设：多个 OpenAI 兼容第三方端点（各平台最新旗舰模型，2026-09 更新），前端可切换
# hint：告知用户去哪里申请该平台的 API Key
MODEL_PRESETS = [
    {
        "id": "glm",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-5.3", "glm-5.3-flash", "glm-5.3-flashx", "glm-5.2",
                   "glm-4.6", "glm-4.5-air", "glm-4-flash"],
        "hint": "在 open.bigmodel.cn 控制台获取 API Key；GLM-5.3 为当前旗舰",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek 深度求索",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner", "deepseek-v4",
                   "deepseek-v4.1-flash", "deepseek-v4-pro"],
        "hint": "在 platform.deepseek.com 获取 API Key；deepseek-chat 始终指向最新对话模型",
    },
    {
        "id": "qwen",
        "name": "通义千问 Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen3.8-max", "qwen3.8-omni-flash", "qwen3.7-plus",
                   "qwen3-max", "qwen-plus", "qwen-flash"],
        "hint": "在 bailian.aliyun.com（百炼）获取 API Key",
    },
    {
        "id": "moonshot",
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k3", "kimi-k3-thinking", "kimi-latest"],
        "hint": "在 platform.moonshot.cn 获取 API Key；K2.5 与 moonshot-v1 已下线",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-5.2", "gpt-5.2-thinking", "gpt-5.2-instant",
                   "gpt-5", "gpt-4.1"],
        "hint": "在 platform.openai.com 获取 API Key（需支持访问）",
    },
    {
        "id": "doubao",
        "name": "豆包（火山方舟）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-seed-2.1", "doubao-seed-1.6", "doubao-seed-1.6-flash",
                   "doubao-1.5-thinking-pro"],
        "hint": "在 console.volcengine.com/ark 获取 API Key；模型名以方舟控制台为准，可直接输入",
    },
    {
        "id": "siliconflow",
        "name": "硅基流动 SiliconFlow（聚合）",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["deepseek-ai/DeepSeek-V4.1-Flash", "Qwen/Qwen3.8-Max",
                   "zai-org/GLM-5.3", "moonshotai/Kimi-K3"],
        "hint": "在 cloud.siliconflow.cn 获取 API Key，一个 Key 调用多家最新开源模型",
    },
    {
        "id": "ollama",
        "name": "本地模型（Ollama）",
        "base_url": "http://localhost:11434/v1",
        "models": ["qwen3:14b", "glm4:9b", "deepseek-r1:14b", "llama3.1:8b"],
        "hint": "本机安装 Ollama 后即可使用：数据完全不出本机、不消耗云端额度、无需 API Key。"
                "模型名以 `ollama list` 为准，可直接输入；生成速度取决于本机配置。",
    },
    {
        "id": "lmstudio",
        "name": "本地模型（LM Studio）",
        "base_url": "http://localhost:1234/v1",
        "models": [],
        "hint": "LM Studio 开启 Local Server 后即可使用：数据完全不出本机、无需 API Key。"
                "模型名以 LM Studio 加载的模型为准，可直接输入。",
    },
    {
        "id": "custom",
        "name": "自定义（OpenAI 兼容）",
        "base_url": "",
        "models": [],
        "hint": "填写任意 OpenAI 兼容端点地址与模型名；指向本机/局域网地址即为本地模式（无需 Key、超时自动放宽）",
    },
]

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}


def is_local_base_url(base_url):
    """判断端点是否为本机/局域网（决定：无需 Key、超时放宽、跳过备用模型链）。"""
    try:
        from urllib.parse import urlparse
        host = (urlparse(base_url or "").hostname or "").strip().lower()
        if not host:
            return False
        if host in _LOCAL_HOSTS:
            return True
        if host.startswith("10.") or host.startswith("192.168.") \
                or host.startswith("169.254."):
            return True
        if host.startswith("172."):
            try:
                seg = int(host.split(".")[1])
                return 16 <= seg <= 31
            except (IndexError, ValueError):
                return False
        return False
    except Exception:
        return False


def _load_env():
    """读取 .env（不存在返回空），支持 KEY=VALUE 与 # 注释。"""
    env = dict(os.environ)
    path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


ENV = _load_env()


def credential_path():
    """API Key 存储位置（用户目录，不随 exe / 项目目录分发）。

    Windows: %APPDATA%\\NexEdu\\credentials.json；其他：~/.nexedu/credentials.json
    """
    base = os.environ.get("APPDATA")
    if base:
        return os.path.join(base, "NexEdu", "credentials.json")
    return os.path.join(os.path.expanduser("~"), ".nexedu", "credentials.json")


def user_settings_path():
    """用户级配置文件：与 API Key 同目录，源码运行 / 打包 exe 共用同一份。

    背景（2026-09-25 排障）：配置原本只存在 ``app_dir/settings.json``，
    打包后 app_dir = exe 所在目录，于是「源码里配好的快模型」在 exe 里读不到，
    回落到内置旗舰默认 glm-5.3，一次对话要 9.6 分钟。迁到这里后，
    清理 dist、换目录、重装 exe 都不会再丢模型配置。
    """
    return os.path.join(os.path.dirname(credential_path()), "settings.json")


def _read_cred():
    """从用户目录凭证文件读取 Key（不存在或损坏返回空）。"""
    p = credential_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return (json.load(f).get("api_key") or "").strip()
        except (json.JSONDecodeError, OSError):
            pass
    return ""


def _write_cred(key):
    """写入 Key 到用户目录；空值表示删除凭证文件。失败不抛异常。"""
    p = credential_path()
    try:
        if not key:
            if os.path.exists(p):
                os.remove(p)
            return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"api_key": key}, f, ensure_ascii=False)
        os.replace(tmp, p)
        try:  # POSIX 下收紧权限；Windows 忽略
            os.chmod(p, 0o600)
        except OSError:
            pass
    except OSError:
        pass


# 运行时配置（可变，界面保存后可热更新）
_api_key = ""
_base_url = DEFAULT_BASE_URL
_model = DEFAULT_MODEL
_provider = "glm"

# 按任务选型（设计文档 §13）：留空表示沿用主模型。
# 例如把 coordinator（回流卡等轻任务）指向 flash 类模型以省成本与延迟。
_routing = {"aim": "", "des": "", "sca": "", "coordinator": ""}

# 备用模型链：主模型不可用时按顺序自动切换（同一 base_url 下）
_fallback_models = []

# 教案输出目录：留空 = 使用默认沙盒 workspace/lessons；
# 指定后，生成的 Word/Markdown 会直接落在该目录（同时保留沙盒副本供界面查看）
_output_dir = ""

# 估算成本单价（元 / 百万 token）：留空不显示成本估算
_cost_per_mtok = ""

# 可选能力开关（教师可在设置里改，默认关闭的都不额外消耗调用次数）：
#   negotiate_parallel  同轮多议题并行回应（省墙钟时间，不增加调用次数，默认开）
#   deep_review         对齐度处于灰区时加一轮深入复核（多一次调用，默认关）
_flags = {"negotiate_parallel": True, "deep_review": False}


# 旧位置（exe 所在目录 / 项目根）：仅用于一次性迁移，之后以用户级配置为准
LEGACY_SETTINGS_PATH = SETTINGS_PATH


def active_settings_path():
    """当前生效的配置文件：用户级优先；首次运行从旧位置迁移一次。

    这样源码运行、打包 exe、换目录或清理 dist 都共用同一份模型配置，
    不会再出现「源码里秒回、exe 里落到默认旗舰慢模型」的情况。
    """
    up = user_settings_path()
    if os.path.exists(up):
        return up
    if os.path.exists(LEGACY_SETTINGS_PATH):
        try:
            os.makedirs(os.path.dirname(up), exist_ok=True)
            with open(LEGACY_SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.pop("api_key", None)   # 密钥不进这份配置
                tmp = up + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, up)
                return up
        except (json.JSONDecodeError, OSError):
            pass
    return LEGACY_SETTINGS_PATH


def _load_settings():
    global _api_key, _base_url, _model, _provider, _output_dir, _cost_per_mtok
    s = {}
    path = active_settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                s = json.load(f)
        except (json.JSONDecodeError, OSError):
            s = {}
    if s.get("base_url"):
        _base_url = s["base_url"].strip()
    if s.get("model"):
        _model = s["model"].strip()
    _provider = s.get("provider") or _provider
    rt = s.get("routing")
    if isinstance(rt, dict):
        for k in _routing:
            if isinstance(rt.get(k), str):
                _routing[k] = rt[k].strip()
    fb = s.get("fallback_models")
    if isinstance(fb, list):
        _fallback_models[:] = [str(x).strip() for x in fb if str(x).strip()]
    _output_dir = (s.get("output_dir") or "").strip()
    if "cost_per_mtok" in s:
        _cost_per_mtok = str(s.get("cost_per_mtok") or "").strip()
    fl = s.get("flags")
    if isinstance(fl, dict):
        for k, v in fl.items():
            _flags[str(k)] = bool(v)

    # Key 只从用户目录凭证读取；旧版本写在 settings.json 里的 Key 迁移后清除
    _api_key = _read_cred()
    legacy = (s.get("api_key") or "").strip()
    if legacy and not _api_key:
        _api_key = legacy
        _write_cred(legacy)
    if "api_key" in s:
        _persist()  # 旧格式配置：回写一次，把 api_key 字段彻底移除
    if not _api_key:
        _api_key = ENV.get("GLM_API_KEY", "").strip()
        if _api_key and not os.path.exists(path):
            _base_url = ENV.get("GLM_BASE_URL", DEFAULT_BASE_URL).strip()
            _model = ENV.get("GLM_MODEL", DEFAULT_MODEL).strip()
            _provider = "glm"


def get_api_key():
    return _api_key


def get_base_url():
    return _base_url


def get_model(task=None):
    """返回模型名；指定 task 时优先用该任务的路由模型。"""
    if task:
        routed = (_routing.get(task) or "").strip()
        if routed:
            return routed
    return _model


def get_provider():
    return _provider


def get_routing():
    return dict(_routing)


def get_fallback_models():
    """备用模型链（主模型失败时按序切换）。"""
    return list(_fallback_models)


def get_output_dir():
    """教案输出目录；空表示使用默认沙盒。"""
    return _output_dir


def set_output_dir(path):
    """设置输出目录（空字符串表示恢复默认沙盒）。"""
    global _output_dir
    _output_dir = (path or "").strip()
    _persist()
    return _output_dir


def resolve_output_dir():
    """实际使用的输出目录：优先用户指定，否则默认沙盒（不存在则创建）。"""
    path = (_output_dir or "").strip()
    if not path:
        from . import lessons
        path = lessons.LESSONS_DIR
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        from . import lessons
        path = lessons.LESSONS_DIR
        os.makedirs(path, exist_ok=True)
    return path


def current():
    """返回当前配置概要（不泄露完整 Key）。"""
    return {
        "api_key_set": bool(_api_key),
        "api_key_masked": _mask(_api_key),
        "base_url": _base_url,
        "model": _model,
        "provider": _provider,
        "key_storage": credential_path(),
        "settings_storage": active_settings_path(),
        "workspace_dir": WORKSPACE_DIR,
        "local_endpoint": is_local_base_url(_base_url),
        "routing": dict(_routing),
        "fallback_models": list(_fallback_models),
        "output_dir": _output_dir,
        "cost_per_mtok": _cost_per_mtok,
        "flags": dict(_flags),
        "folder_picker": True,
    }


def _mask(key):
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "****" + key[-4:]


def apply_settings(data):
    """应用并持久化界面传入的配置，返回保存后的概要。

    API Key 只写入用户目录凭证文件；settings.json 不含任何密钥，
    可随程序目录自由拷贝/分发。
    """
    global _api_key, _base_url, _model, _provider, _output_dir, _cost_per_mtok
    if data.get("clear_key"):
        # 显式清除已保存的 Key
        _api_key = ""
        _write_cred("")
    elif data.get("api_key") and str(data.get("api_key")).strip():
        # 仅当提供了非空 Key 时才更新（留空沿用旧 Key）
        _api_key = str(data["api_key"]).strip()
        _write_cred(_api_key)
    if "base_url" in data:
        _base_url = (data.get("base_url") or DEFAULT_BASE_URL).strip()
    if "model" in data:
        _model = (data.get("model") or DEFAULT_MODEL).strip()
    if data.get("provider"):
        _provider = data["provider"]
    rt = data.get("routing")
    if isinstance(rt, dict):
        for k in _routing:
            if k in rt:
                _routing[k] = str(rt.get(k) or "").strip()
    fb = data.get("fallback_models")
    if isinstance(fb, list):
        _fallback_models[:] = [str(x).strip() for x in fb if str(x).strip()]
    elif isinstance(fb, str):
        _fallback_models[:] = [x.strip() for x in fb.split(",") if x.strip()]
    if "output_dir" in data:
        _output_dir = str(data.get("output_dir") or "").strip()
    if "cost_per_mtok" in data:
        _cost_per_mtok = str(data.get("cost_per_mtok") or "").strip()
    fl = data.get("flags")
    if isinstance(fl, dict):
        for k, v in fl.items():
            _flags[str(k)] = bool(v)
    _persist()
    return current()


def _persist():
    """持久化非敏感配置（绝不包含 API Key）。

    主写用户级配置（源码/exe 共用）；若旧位置已有配置文件，同步写一份，
    保证便携目录里的旧版本仍能读到相同配置，不会两边不一致。
    """
    data = {
        "base_url": _base_url,
        "model": _model,
        "provider": _provider,
        "routing": dict(_routing),
        "fallback_models": list(_fallback_models),
        "output_dir": _output_dir,
        "cost_per_mtok": _cost_per_mtok,
        "flags": dict(_flags),
    }
    targets = [user_settings_path()]
    if os.path.exists(LEGACY_SETTINGS_PATH):
        targets.append(LEGACY_SETTINGS_PATH)
    for p in targets:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        except OSError:
            pass


# 运行参数
SERVER_HOST = ENV.get("NEXEDU_HOST", "127.0.0.1").strip()
SERVER_PORT = int(ENV.get("NEXEDU_PORT", "8520"))
OPEN_BROWSER = ENV.get("NEXEDU_OPEN_BROWSER", "1") == "1"

_load_settings()
