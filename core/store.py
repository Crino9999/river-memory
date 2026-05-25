"""存储层：Chroma + SQLite（纯离线：TF-IDF + 随机投影，不走网络）"""
import sqlite3, json, chromadb, os, hashlib
from typing import List, Tuple, Optional
from chromadb.config import Settings
from sklearn.feature_extraction.text import TfidfVectorizer
import numpy as np
from config import CHROMA_DIR, DB_PATH
from core.memory import Memory

# 简化的文本嵌入：TF-IDF → 128维（纯本地，零网络依赖）
_vec = TfidfVectorizer(max_features=128)

def _hash_to_seed(text: str) -> int:
    return int(hashlib.md5(text.encode()).hexdigest()[:8], 16)

def embed_texts(texts: List[str]) -> List[List[float]]:
    """纯本地嵌入：TF-IDF + 随机投影填充到128维"""
    global _vec
    try:
        tfidf = _vec.fit_transform(texts).toarray()
    except ValueError:
        _vec = TfidfVectorizer(max_features=128)
        tfidf = _vec.fit_transform(texts).toarray()
    # 填充到128维
    results = []
    for row in tfidf:
        vec = list(row.astype(float))
        np.random.seed(_hash_to_seed(texts[0] if len(vec) < 2 else str(vec[:10])))
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
        self._db.commit()

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
