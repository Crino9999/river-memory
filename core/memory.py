"""数据模型：Memory 和 EventStream"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional

# 生命周期状态
PENDING = "pending"      # 待处理（承诺未兑现、事件进行中）
ACTIVE = "active"         # 当前有效状态
RESOLVED = "resolved"     # 已完成/已解决
SUPERSEDED = "superseded" # 被新状态覆盖
INVALID = "invalid"       # 无效（梦境、玩笑、已取消）
DREAM = "dream"           # 梦境记忆（非真实发生）

@dataclass
class Memory:
    memory_id: str
    content: str                    # 第一人称主观记忆
    embedding: Optional[List[float]] = None
    timestamp: str = ""             # YYYY-MM-DD（记录时间）
    event_stream_id: str = ""
    objects: List[str] = field(default_factory=list)
    environment: str = ""
    status_update: Optional[str] = None  # 如 "拉姆的角=已治愈"
    # v0.3 新增：生命周期与溯源字段
    occurred_at: str = ""           # 事件实际发生日期（可能不同于记录时间）
    due_at: Optional[str] = None    # 承诺到期日
    trigger_at: Optional[str] = None # 触发提醒的日期
    valid_from: str = ""            # 记忆生效起始日
    valid_to: Optional[str] = None  # 记忆失效日
    lifecycle: str = ACTIVE         # pending/active/resolved/superseded/invalid/dream
    confidence: float = 1.0         # 入库置信度 (0-1)
    source_event_id: Optional[str] = None  # 来源事件流
    supersedes: Optional[str] = None  # 此记忆替代了哪条旧记忆

    def to_dict(self):
        d = asdict(self)
        d.pop("embedding", None)
        return d

    @property
    def is_promise(self) -> bool:
        """是否为未完成的承诺"""
        return self.lifecycle == PENDING and self.due_at is not None

    @property
    def is_overdue(self) -> bool:
        """承诺是否已过期未兑现"""
        if not self.is_promise:
            return False
        from datetime import datetime
        try:
            due = datetime.strptime(self.due_at, "%Y-%m-%d")
            return due <= datetime.now()
        except (ValueError, TypeError):
            return False

class EventStream:
    """同一事件链的记忆按时间排列"""
    def __init__(self, event_id: str, memories: List[Memory] = None):
        self.event_id = event_id
        self.memories: List[Memory] = sorted(memories or [], key=lambda m: m.timestamp)

    def latest(self) -> Memory:
        """取最新一条记忆"""
        return self.memories[-1] if self.memories else None

    def current_state(self) -> Optional[Memory]:
        """
        当前有效状态投影：跳过无效/梦境/已废弃的记忆，取最新的有效状态。
        解决梦境、玩笑、复述旧事等场景下的状态污染问题。
        """
        valid = [m for m in self.memories
                 if m.lifecycle not in (INVALID, DREAM, SUPERSEDED)]
        return valid[-1] if valid else None

    def all_versions(self) -> List[Memory]:
        return self.memories

    def add(self, mem: Memory):
        mem.event_stream_id = self.event_id
        self.memories.append(mem)
        self.memories.sort(key=lambda m: m.timestamp)
