# River Memory System Config
import os
from pathlib import Path

def _load_dotenv():
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key not in os.environ:
                    os.environ[key] = val

_load_dotenv()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default

def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key, str(default)).lower()
    return val in ("true", "1", "yes", "on")

# LLM API 配置
API_BASE = _env("API_BASE", "http://127.0.0.1:18789/v1")
API_KEY = _env("API_KEY", "")
MODEL = _env("MODEL", "deepseek-v4-flash")

# Embedding 模型
EMBED_MODEL = _env("EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

# 存储路径（基于项目根目录的绝对路径，防止 cwd 不同导致数据丢失）
BASE_DIR = Path(__file__).parent
CHROMA_DIR = _env("CHROMA_DIR", str(BASE_DIR / "chroma_data"))
DB_PATH = _env("DB_PATH", str(BASE_DIR / "river.db"))

# 检索配置
TOP_K = _env_int("TOP_K", 5)

# 探针权重
PROBE_WEIGHTS = {
    "semantic": _env_int("PROBE_WEIGHT_SEMANTIC", 1),
    "time": _env_int("PROBE_WEIGHT_TIME", 2),
    "object": _env_int("PROBE_WEIGHT_OBJECT", 3),
    "env": _env_int("PROBE_WEIGHT_ENV", 1),
}

# LLM 调用配置
LLM_MAX_RETRIES = _env_int("LLM_MAX_RETRIES", 3)
LLM_RETRY_DELAY = _env_float("LLM_RETRY_DELAY", 1.0)
LLM_TIMEOUT = _env_int("LLM_TIMEOUT", 60)

# 日志配置
LOG_LEVEL = _env("LOG_LEVEL", "INFO")
LOG_FILE = _env("LOG_FILE", str(BASE_DIR / "river.log"))

# 会话配置
SESSION_MAX_HISTORY = _env_int("SESSION_MAX_HISTORY", 20)
SESSION_AUTO_INGEST = _env_bool("SESSION_AUTO_INGEST", True)

# 去重配置
DEDUP_WINDOW_DAYS = _env_int("DEDUP_WINDOW_DAYS", 1)

# Embedding 引擎
USE_REAL_EMBED = _env_bool("USE_REAL_EMBED", True)
