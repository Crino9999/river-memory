"""数据模型：Memory 和 EventStream"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional

@dataclass
class Memory:
    memory_id: str
    content: str                    # 第一人称主观记忆
    embedding: Optional[List[float]] = None
    timestamp: str = ""             # YYYY-MM-DD
    event_stream_id: str = ""
    objects: List[str] = field(default_factory=list)
    environment: str = ""
    status_update: Optional[str] = None  # 如 "拉姆的角=已治愈"

    def to_dict(self):
        d = asdict(self)
        d.pop("embedding", None)
        return d

class EventStream:
    """同一事件链的记忆按时间排列"""
    def __init__(self, event_id: str, memories: List[Memory] = None):
        self.event_id = event_id
        self.memories: List[Memory] = sorted(memories or [], key=lambda m: m.timestamp)

    def latest(self) -> Memory:
        return self.memories[-1]

    def all_versions(self) -> List[Memory]:
        return self.memories

    def add(self, mem: Memory):
        mem.event_stream_id = self.event_id
        self.memories.append(mem)
        self.memories.sort(key=lambda m: m.timestamp)
