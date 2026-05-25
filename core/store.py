"""存储层：Chroma + SQLite（纯离线：jieba分词 + 固定TF-IDF坐标系）"""
import sqlite3, json, chromadb, os
from typing import List, Tuple, Optional
from chromadb.config import Settings
from sklearn.feature_extraction.text import TfidfVectorizer
import numpy as np
import jieba
from config import CHROMA_DIR, DB_PATH, USE_REAL_EMBED
from core.memory import Memory

SCHEMA_VERSION = 2

def _tokenize(text: str) -> str:
    """中文分词，空格分隔"""
    return " ".join(jieba.cut(text))

# 种子语料 —— 确保TF-IDF词表一致
_SEED_CORPUS = [
    _tokenize("欠钱还钱承诺债务借条还款到期"),
    _tokenize("治好治愈治疗恢复健康受伤伤口"),
    _tokenize("今天天气不错心情好累疲惫开心"),
    _tokenize("聊天吃饭睡觉日常闲聊问候你好"),
    _tokenize("战斗攻击防御技能魔法冒险地下城"),
]
_vec = TfidfVectorizer(max_features=128)
_vec.fit(_SEED_CORPUS)

_real_model = None

def _get_real_model():
    global _real_model
    if _real_model is None:
        if not USE_REAL_EMBED:
            _real_model = False
            return None
        try:
            from sentence_transformers import SentenceTransformer
            from config import EMBED_MODEL
            _real_model = SentenceTransformer(EMBED_MODEL)
        except Exception:
            _real_model = False
    return _real_model if _real_model else None

def embed_texts(texts: List[str]) -> List[List[float]]:
    """嵌入：优先使用 sentence-transformers 真实模型，降级到 TF-IDF"""
    model = _get_real_model()
    if model:
        embeddings = model.encode(texts, normalize_embeddings=True)
        return [list(vec.astype(float)) for vec in embeddings]

    global _vec
    tokenized = [_tokenize(t) for t in texts]
    tfidf = _vec.transform(tokenized).toarray()
    results = []
    for row in tfidf:
        vec = list(row.astype(float))
        np.random.seed(int(sum(abs(v) for v in vec) * 1e6) % 2**31)
        while len(vec) < 128:
            vec.append(float(np.random.randn() * 0.01))
        results.append(vec[:128])
    return results

class MemoryStore:
    def __init__(self):
        os.makedirs(CHROMA_DIR, exist_ok=True)

        self._chroma = chromadb.PersistentClient(
            path=CHROMA_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._chroma.get_or_create_collection("river_memories")

        self._db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                content TEXT,
                timestamp TEXT,
                event_stream_id TEXT,
                objects TEXT,
                environment TEXT,
                status_update TEXT
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._migrate()
        self._ensure_indexes()
        self._db.commit()

    def _migrate(self):
        row = self._db.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        current = row[0] or 0

        if current < 1:
            self._db.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (1,)
            )
            current = 1

        if current < 2:
            self._ensure_indexes()
            self._db.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (2,)
            )

    def _ensure_indexes(self):
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_event_stream ON memories(event_stream_id)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_ts_stream ON memories(timestamp, event_stream_id)"
        )

    def add(self, mem: Memory):
        emb = embed_texts([mem.content])[0]
        mem.embedding = emb

        self._collection.add(
            ids=[mem.memory_id],
            embeddings=[emb],
            metadatas=[{"content": mem.content, "timestamp": mem.timestamp}],
        )

        self._db.execute(
            """INSERT OR REPLACE INTO memories VALUES (?,?,?,?,?,?,?)""",
            (mem.memory_id, mem.content, mem.timestamp, mem.event_stream_id,
             json.dumps(mem.objects), mem.environment, mem.status_update),
        )
        self._db.commit()

    def vector_search(self, query_emb: List[float], top_k: int = 5) -> List[Tuple[Memory, float]]:
        results = self._collection.query(query_embeddings=[query_emb], n_results=top_k)
        out = []
        if results["ids"] and results["ids"][0]:
            for mid, dist in zip(results["ids"][0], results["distances"][0] or []):
                mem = self.get_by_id(mid)
                if mem:
                    out.append((mem, 1.0 - min(dist, 1.0)))
        return out

    def get_by_id(self, memory_id: str) -> Optional[Memory]:
        row = self._db.execute(
            "SELECT * FROM memories WHERE memory_id=?", (memory_id,)
        ).fetchone()
        if not row:
            return None
        return Memory(
            memory_id=row[0], content=row[1], timestamp=row[2],
            event_stream_id=row[3], objects=json.loads(row[4] or "[]"),
            environment=row[5] or "", status_update=row[6],
        )

    def list_all(self) -> List[Memory]:
        rows = self._db.execute("SELECT * FROM memories").fetchall()
        return [Memory(
            memory_id=r[0], content=r[1], timestamp=r[2],
            event_stream_id=r[3], objects=json.loads(r[4] or "[]"),
            environment=r[5] or "", status_update=r[6],
        ) for r in rows]

    def get_by_stream(self, event_stream_id: str) -> List[Memory]:
        rows = self._db.execute(
            "SELECT * FROM memories WHERE event_stream_id=? ORDER BY timestamp",
            (event_stream_id,),
        ).fetchall()
        return [Memory(
            memory_id=r[0], content=r[1], timestamp=r[2],
            event_stream_id=r[3], objects=json.loads(r[4] or "[]"),
            environment=r[5] or "", status_update=r[6],
        ) for r in rows]

    def clear(self):
        self._db.execute("DELETE FROM memories")
        self._db.commit()
        try:
            self._chroma.delete_collection("river_memories")
            self._collection = self._chroma.create_collection("river_memories")
        except Exception:
            pass
