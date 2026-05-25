# River Memory System Config
import os

API_BASE = "https://opencode.ai/zen/go/v1"
API_KEY = os.environ.get("OPENCODE_API_KEY", "")
MODEL = "deepseek-v4-flash"
EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
CHROMA_DIR = "./chroma_data"
DB_PATH = "./river.db"
