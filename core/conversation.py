"""会话管理：多用户会话上下文 + 物理坐标维护 + 自动入库"""
import time
import threading
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from core.memory import Memory
from core.store import MemoryStore
from core.ingestor import MemoryIngestor
from core.logger import get_logger
from config import SESSION_MAX_HISTORY, SESSION_AUTO_INGEST

log = get_logger(__name__)


class Session:
    """单个用户会话，持有当前物理坐标和对话历史"""

    def __init__(self, session_id: str, character_name: str = "角色"):
        self.session_id = session_id
        self.character_name = character_name
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.present_people: List[str] = []
        self.current_env: str = ""
        self.history: List[Dict] = []
        self.last_active = time.time()

    def add_turn(self, user_msg: str, bot_reply: str):
        self.history.append({
            "user": user_msg,
            "bot": bot_reply,
            "date": self.current_date,
            "people": list(self.present_people),
            "env": self.current_env,
        })
        if len(self.history) > SESSION_MAX_HISTORY * 2:
            self.history = self.history[-SESSION_MAX_HISTORY * 2:]
        self.last_active = time.time()

    def update_coordinates(self, people: List[str] = None, env: str = None, date: str = None):
        """手动更新物理坐标"""
        if people is not None:
            self.present_people = people
        if env is not None:
            self.current_env = env
        if date is not None:
            self.current_date = date

    def advance_date(self, days: int = 1):
        """推进会话日期"""
        try:
            dt = datetime.strptime(self.current_date, "%Y-%m-%d") + timedelta(days=int(days))
            self.current_date = dt.strftime("%Y-%m-%d")
        except ValueError:
            pass


class ConversationManager:
    """管理多用户/多会话上下文，协调记忆入库和检索"""

    def __init__(self, store: MemoryStore = None):
        self._store = store or MemoryStore()
        self._ingestor = MemoryIngestor(self._store)
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_session(self, session_id: str, character_name: str = "角色") -> Session:
        """获取或创建会话"""
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = Session(session_id, character_name)
                log.info("Created new session: %s (character=%s)", session_id, character_name)
            return self._sessions[session_id]

    def process_turn(
        self,
        session_id: str,
        user_msg: str,
        bot_reply: str,
        character_name: str = "角色",
    ) -> List[Memory]:
        """
        处理一轮对话：更新坐标 → 记录历史 → 自动入库
        返回本次入库的 Memory 列表
        """
        session = self.get_session(session_id, character_name)

        ingested = []
        if SESSION_AUTO_INGEST:
            ingested = self._ingestor.ingest(
                user_msg=user_msg,
                bot_reply=bot_reply,
                current_date=session.current_date,
                present_people=session.present_people,
                current_env=session.current_env,
            )

        session.add_turn(user_msg, bot_reply)
        return ingested

    def recall_for_session(self, session_id: str, user_input: str) -> str:
        """
        为指定会话执行完整检索
        """
        from main import recall
        session = self.get_session(session_id)
        return recall(
            user_input=user_input,
            current_date=session.current_date,
            present_people=session.present_people,
            current_env=session.current_env,
            store=self._store,
            character_name=session.character_name,
        )

    def force_ingest_recent_history(self, session_id: str, turns: int = None):
        """强制将最近N轮历史入库（用于手动触发或会话结束）"""
        session = self.get_session(session_id)
        if turns is None:
            turns = len(session.history)
        target = session.history[-turns:]
        for turn in target:
            self._ingestor.ingest(
                user_msg=turn["user"],
                bot_reply=turn["bot"],
                current_date=turn["date"],
                present_people=turn["people"],
                current_env=turn["env"],
            )

    def list_sessions(self) -> List[str]:
        with self._lock:
            return list(self._sessions.keys())

    def cleanup_idle_sessions(self, max_idle_seconds: float = 3600):
        """清理空闲超时的会话"""
        now = time.time()
        with self._lock:
            idle = [
                sid for sid, s in self._sessions.items()
                if now - s.last_active > max_idle_seconds
            ]
            for sid in idle:
                log.info("Cleaning up idle session: %s", sid)
                del self._sessions[sid]

    @property
    def store(self) -> MemoryStore:
        return self._store

    @property
    def ingestor(self) -> MemoryIngestor:
        return self._ingestor
