"""存储层：Chroma + SQLite（纯离线：jieba分词 + 固定TF-IDF坐标系）"""
import sqlite3, json, chromadb, os
from typing import List, Tuple, Optional
from chromadb.config import Settings
from sklearn.feature_extraction.text import TfidfVectorizer
import numpy as np
import jieba
from config import CHROMA_DIR, DB_PATH, USE_REAL_EMBED
from core.memory import Memory

SCHEMA_VERSION = 3

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

def _row_to_memory(row) -> Memory:
    """Row → Memory 对象，兼容旧表（缺少新字段时用默认值）"""
    cols = row.keys()
    return Memory(
        memory_id=row["memory_id"],
        content=row["content"],
        timestamp=row["timestamp"],
        event_stream_id=row["event_stream_id"] or "",
        objects=json.loads(row["objects"] or "[]"),
        environment=row["environment"] or "",
        status_update=row["status_update"],
        occurred_at=row["occurred_at"] if "occurred_at" in cols else "",
        due_at=row["due_at"] if "due_at" in cols else None,
        trigger_at=row["trigger_at"] if "trigger_at" in cols else None,
        valid_from=row["valid_from"] if "valid_from" in cols else "",
        valid_to=row["valid_to"] if "valid_to" in cols else None,
        lifecycle=row["lifecycle"] if "lifecycle" in cols else "active",
        confidence=row["confidence"] if "confidence" in cols else 1.0,
        source_event_id=row["source_event_id"] if "source_event_id" in cols else None,
        supersedes=row["supersedes"] if "supersedes" in cols else None,
    )


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
                status_update TEXT,
                occurred_at TEXT DEFAULT '',
                due_at TEXT,
                trigger_at TEXT,
                valid_from TEXT DEFAULT '',
                valid_to TEXT,
                lifecycle TEXT DEFAULT 'active',
                confidence REAL DEFAULT 1.0,
                source_event_id TEXT,
                supersedes TEXT
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

        if current < 3:
            # v3: 新增生命周期和溯源字段
            new_cols = [
                "ALTER TABLE memories ADD COLUMN occurred_at TEXT DEFAULT ''",
                "ALTER TABLE memories ADD COLUMN due_at TEXT",
                "ALTER TABLE memories ADD COLUMN trigger_at TEXT",
                "ALTER TABLE memories ADD COLUMN valid_from TEXT DEFAULT ''",
                "ALTER TABLE memories ADD COLUMN valid_to TEXT",
                "ALTER TABLE memories ADD COLUMN lifecycle TEXT DEFAULT 'active'",
                "ALTER TABLE memories ADD COLUMN confidence REAL DEFAULT 1.0",
                "ALTER TABLE memories ADD COLUMN source_event_id TEXT",
                "ALTER TABLE memories ADD COLUMN supersedes TEXT",
            ]
            for sql in new_cols:
                try:
                    self._db.execute(sql)
                except sqlite3.OperationalError:
                    pass  # 列已存在
            self._db.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (3,)
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
            """INSERT OR REPLACE INTO memories
               (memory_id, content, timestamp, event_stream_id, objects,
                environment, status_update, occurred_at, due_at, trigger_at,
                valid_from, valid_to, lifecycle, confidence, source_event_id, supersedes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mem.memory_id, mem.content, mem.timestamp, mem.event_stream_id,
             json.dumps(mem.objects), mem.environment, mem.status_update,
             mem.occurred_at or "", mem.due_at, mem.trigger_at,
             mem.valid_from or "", mem.valid_to, mem.lifecycle or "active",
             mem.confidence, mem.source_event_id, mem.supersedes),
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
        return _row_to_memory(row)

    def list_all(self) -> List[Memory]:
        rows = self._db.execute("SELECT * FROM memories").fetchall()
        return [_row_to_memory(r) for r in rows]

    def get_by_stream(self, event_stream_id: str) -> List[Memory]:
        rows = self._db.execute(
            "SELECT * FROM memories WHERE event_stream_id=? ORDER BY timestamp",
            (event_stream_id,),
        ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def clear(self):
        self._db.execute("DELETE FROM memories")
        self._db.commit()
        try:
            self._chroma.delete_collection("river_memories")
            self._collection = self._chroma.create_collection("river_memories")
        except Exception:
            pass
