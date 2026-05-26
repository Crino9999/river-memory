"""存储层：Chroma + SQLite（纯离线：jieba分词 + 固定TF-IDF坐标系）"""
import sqlite3, json, chromadb, os
from typing import List, Tuple, Optional
from chromadb.config import Settings
from sklearn.feature_extraction.text import TfidfVectorizer
import numpy as np
import jieba
from config import CHROMA_DIR, DB_PATH, USE_REAL_EMBED
from core.memory import Memory

SCHEMA_VERSION = 4

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

def _safe_json(val):
    """安全 JSON 解析，失败返回 None"""
    if not val:
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


def _row_to_memory(row) -> Memory:
    """Row → Memory 对象，兼容旧表（缺少新字段时用默认值）"""
    cols = row.keys()
    return Memory(
        memory_id=row["memory_id"],
        content=row["content"],
        timestamp=row["timestamp"],
        event_stream_id=row["event_stream_id"] or "",
        objects=_safe_json(row["objects"]) or [],
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
        # v4 新字段
        salience=row["salience"] if "salience" in cols else 5,
        volatility=row["volatility"] if "volatility" in cols else "medium",
        stability=row["stability"] if "stability" in cols else 2.3,
        difficulty=row["difficulty"] if "difficulty" in cols else 5.0,
        last_accessed=row["last_accessed"] if "last_accessed" in cols else "",
        access_count=row["access_count"] if "access_count" in cols else 0,
        version=row["version"] if "version" in cols else 0,
        provenance_round_hash=row["provenance_round_hash"] if "provenance_round_hash" in cols else "",
        provenance_context=row["provenance_context"] if "provenance_context" in cols else "",
        reinterpretation=row["reinterpretation"] if "reinterpretation" in cols else None,
        correction_history=_safe_json(row["correction_history"]) if ("correction_history" in cols and row["correction_history"]) else None,
        related_streams=_safe_json(row["related_streams"]) if "related_streams" in cols else [],
        link_strength=_safe_json(row["link_strength"]) if "link_strength" in cols else [],
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
                supersedes TEXT,
                salience INTEGER DEFAULT 5,
                volatility TEXT DEFAULT 'medium',
                stability REAL DEFAULT 2.3,
                difficulty REAL DEFAULT 5.0,
                last_accessed TEXT DEFAULT '',
                access_count INTEGER DEFAULT 0,
                version INTEGER DEFAULT 0,
                provenance_round_hash TEXT DEFAULT '',
                provenance_context TEXT DEFAULT '',
                reinterpretation TEXT,
                correction_history TEXT,
                related_streams TEXT DEFAULT '[]',
                link_strength TEXT DEFAULT '[]'
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL,
                content TEXT,
                confidence REAL,
                reason TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                reviewed_at TEXT
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

        if current < 4:
            # v4: 新增 v3 salience/FSRS/溯源/再巩固 字段
            v4_cols = [
                "ALTER TABLE memories ADD COLUMN salience INTEGER DEFAULT 5",
                "ALTER TABLE memories ADD COLUMN volatility TEXT DEFAULT 'medium'",
                "ALTER TABLE memories ADD COLUMN stability REAL DEFAULT 2.3",
                "ALTER TABLE memories ADD COLUMN difficulty REAL DEFAULT 5.0",
                "ALTER TABLE memories ADD COLUMN last_accessed TEXT DEFAULT ''",
                "ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0",
                "ALTER TABLE memories ADD COLUMN version INTEGER DEFAULT 0",
                "ALTER TABLE memories ADD COLUMN provenance_round_hash TEXT DEFAULT ''",
                "ALTER TABLE memories ADD COLUMN provenance_context TEXT DEFAULT ''",
                "ALTER TABLE memories ADD COLUMN reinterpretation TEXT",
                "ALTER TABLE memories ADD COLUMN correction_history TEXT",
                "ALTER TABLE memories ADD COLUMN related_streams TEXT DEFAULT '[]'",
                "ALTER TABLE memories ADD COLUMN link_strength TEXT DEFAULT '[]'",
            ]
            for sql in v4_cols:
                try:
                    self._db.execute(sql)
                except sqlite3.OperationalError:
                    pass
            self._db.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (4,)
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
                valid_from, valid_to, lifecycle, confidence, source_event_id, supersedes,
                salience, volatility, stability, difficulty, last_accessed, access_count,
                version, provenance_round_hash, provenance_context,
                reinterpretation, correction_history, related_streams, link_strength)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                       ?,?,?,?,?,?,
                       ?,?,?,
                       ?,?,?,?)""",
            (mem.memory_id, mem.content, mem.timestamp, mem.event_stream_id,
             json.dumps(mem.objects), mem.environment, mem.status_update,
             mem.occurred_at or "", mem.due_at, mem.trigger_at,
             mem.valid_from or "", mem.valid_to, mem.lifecycle or "active",
             mem.confidence, mem.source_event_id, mem.supersedes,
             mem.salience, mem.volatility, mem.stability, mem.difficulty,
             mem.last_accessed, mem.access_count,
             mem.version, mem.provenance_round_hash, mem.provenance_context,
             mem.reinterpretation, json.dumps(mem.correction_history) if mem.correction_history else None,
             json.dumps(mem.related_streams), json.dumps(mem.link_strength)),
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
        self._db.execute("DELETE FROM review_queue")
        self._db.commit()
        try:
            self._chroma.delete_collection("river_memories")
            self._collection = self._chroma.create_collection("river_memories")
        except Exception:
            pass

    # ======== 审查队列 ========

    def add_review(self, memory_id: str, content: str = "", confidence: float = 0.0, reason: str = ""):
        """低置信度记忆放入审查队列"""
        self._db.execute(
            """INSERT INTO review_queue (memory_id, content, confidence, reason)
               VALUES (?,?,?,?)""",
            (memory_id, content[:200], confidence, reason),
        )
        self._db.commit()

    def get_pending_reviews(self) -> list:
        """获取待审查的记忆列表"""
        rows = self._db.execute(
            "SELECT * FROM review_queue WHERE status='pending' ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def resolve_review(self, review_id: int, action: str):
        """
        处理审查: approve / reject / merge
        - approve: 不做额外操作（记忆已入库，只是确认）
        - reject: 删除对应记忆
        - merge: 手动指定归入的 stream_id (需额外调用)
        """
        self._db.execute(
            "UPDATE review_queue SET status=?, reviewed_at=datetime('now') WHERE id=?",
            (action, review_id),
        )
        if action == "reject":
            row = self._db.execute(
                "SELECT memory_id FROM review_queue WHERE id=?", (review_id,)
            ).fetchone()
            if row:
                self._db.execute("DELETE FROM memories WHERE memory_id=?", (row["memory_id"],))
                try:
                    self._collection.delete(ids=[row["memory_id"]])
                except Exception:
                    pass
        self._db.commit()

    # ======== 生命周期管理 ========

    def set_lifecycle(self, memory_id: str, new_state: str):
        """更新单条记忆的生命周期"""
        self._db.execute(
            "UPDATE memories SET lifecycle=? WHERE memory_id=?", (new_state, memory_id)
        )
        self._db.commit()

    def set_stream_lifecycle(self, event_stream_id: str, new_state: str):
        """批量更新整个事件流的生命周期"""
        self._db.execute(
            "UPDATE memories SET lifecycle=? WHERE event_stream_id=?",
            (new_state, event_stream_id),
        )
        self._db.commit()

    def superseed(self, old_memory_id: str, new_memory_id: str):
        """用新记忆替代旧记忆：旧记忆标为 superseded，新记忆设置 supersedes 字段"""
        self._db.execute(
            "UPDATE memories SET lifecycle='superseded' WHERE memory_id=?",
            (old_memory_id,),
        )
        self._db.execute(
            "UPDATE memories SET supersedes=? WHERE memory_id=?",
            (old_memory_id, new_memory_id),
        )
        self._db.commit()

    def get_by_lifecycle(self, state: str) -> list:
        """按生命周期状态查询"""
        rows = self._db.execute(
            "SELECT * FROM memories WHERE lifecycle=? ORDER BY timestamp",
            (state,),
        ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def get_pending_promises(self, current_date: str = None) -> list:
        """获取所有未完成且已过期的承诺"""
        from datetime import datetime
        all_pending = self.get_by_lifecycle("pending")
        if current_date:
            try:
                today = datetime.strptime(current_date, "%Y-%m-%d")
                return [m for m in all_pending
                        if m.due_at and datetime.strptime(m.due_at, "%Y-%m-%d") <= today]
            except (ValueError, TypeError):
                pass
        return all_pending

    # ======== FSRS 强化更新 ========

    def reinforce_memory(self, memory_id: str, stability: float, last_accessed: str, access_count: int):
        """检索后更新 FSRS 统计字段（轻量，不重新建向量）"""
        self._db.execute(
            """UPDATE memories SET stability=?, last_accessed=?, access_count=?
               WHERE memory_id=?""",
            (stability, last_accessed, access_count, memory_id),
        )
        self._db.commit()

    # ======== 动态融流 ========

    def merge_orphan_streams(self, min_nodes: int = 3, idle_days: int = 14) -> int:
        """
        动态融流：清算孤儿流。
        三道安检：1.status_update非空→豁免 2.被related_streams引用→豁免 3.salience前30%→豁免
        返回合并的流数量。
        """
        from datetime import datetime, timedelta
        all_mems = self.list_all()
        if not all_mems:
            return 0

        # 收集所有流
        streams: dict = {}
        for m in all_mems:
            if m.event_stream_id:
                streams.setdefault(m.event_stream_id, []).append(m)

        if not streams:
            return 0

        # 计算引用关系
        referenced_streams = set()
        for m in all_mems:
            for rsid in (m.related_streams or []):
                referenced_streams.add(rsid)

        # 计算 salience 阈值（top 30%）
        stream_saliences = []
        for sid, mems in streams.items():
            avg_s = sum(m.salience for m in mems) / len(mems)
            stream_saliences.append(avg_s)
        stream_saliences.sort(reverse=True)
        cutoff_idx = max(0, int(len(stream_saliences) * 0.3) - 1)
        salience_threshold = stream_saliences[cutoff_idx] if stream_saliences else 10

        merged = 0
        today = datetime.now()

        for sid, mems in streams.items():
            if len(mems) >= min_nodes:
                continue
            # 安检1: status_update 非空
            if any(m.status_update for m in mems):
                continue
            # 安检2: 被引用
            if sid in referenced_streams:
                continue
            # 安检3: salience 前30%
            avg_s = sum(m.salience for m in mems) / len(mems)
            if avg_s >= salience_threshold:
                continue

            # 通过三道安检 → 并入日常背景流
            for m in mems:
                m.event_stream_id = "evt_daily_background"
                self.add(m)
            merged += 1

        if merged:
            self._db.commit()
        return merged
