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
    # v0.5 (v3) 新增：显著性、遗忘曲线、溯源
    salience: int = 5               # 综合显著性 1-10，max(三维)×0.7+mean(三维)×0.3
    volatility: str = "medium"      # 情感易逝性：low/medium/high
    stability: float = 2.3          # S：检索成功率降到90%所需天数
    difficulty: float = 5.0         # D：1-10，D_init = 10 - salience
    last_accessed: str = ""         # 最后被检索命中的时间戳
    access_count: int = 0           # 累计被命中次数
    version: int = 0                # Pending 阶段的版本号（竞态校验）
    provenance_round_hash: str = "" # 固化时所属对话轮次的哈希
    provenance_context: str = ""    # 入库时的上下文摘要
    reinterpretation: Optional[str] = None  # 事后重新诠释（解读重写）
    correction_history: Optional[List[dict]] = None  # 事实更正记录
    related_streams: List[str] = field(default_factory=list)  # 因果关联流
    link_strength: List[float] = field(default_factory=list)   # 与 related_streams 一一对应

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

    def retrievability(self, current_date: str = "") -> float:
        """
        FSRS 检索可用性：R = (1 + FACTOR × elapsed / S) ^ (-DECAY)
        DECAY = 0.1542。距上次访问越久、stability 越低 → R 越低
        """
        if not self.last_accessed:
            return 0.5
        from datetime import datetime
        try:
            last = datetime.strptime(self.last_accessed, "%Y-%m-%d")
            now = datetime.strptime(current_date, "%Y-%m-%d") if current_date else datetime.now()
            elapsed = max(0, (now - last).days)
            s = max(self.stability, 0.1)
            return (1 + elapsed / s) ** (-0.1542)
        except (ValueError, TypeError):
            return 0.5

    @property
    def retrieval_strength(self) -> float:
        """
        提取强度 = R + 惊喜加分
        惊喜加分 = 0.3 × R × (1-R) × 4
        钟形曲线：R≈0.5 时最大 → 间隔效应最优
        """
        R = self.retrievability()
        surprise = 0.3 * R * (1 - R) * 4
        return min(1.0, R + surprise)

    def reinforce(self, current_date: str = ""):
        """
        FSRS 检索后强化：更新 stability + last_accessed + access_count。
        S_new = S × (1 + exp(w8) × (11-D) × S^(-w9) × (exp((1-R)×w10) - 1))
        """
        import math
        from datetime import datetime
        w8, w9, w10 = -0.2, -0.2, 1.5
        R = self.retrievability(current_date)
        D = max(self.difficulty, 1.0)
        S = max(self.stability, 0.1)
        try:
            factor = 1 + math.exp(w8) * (11 - D) * (S ** (-w9)) * (math.exp((1 - R) * w10) - 1)
            self.stability = S * max(factor, 1.0)
        except (OverflowError, ValueError):
            pass
        self.last_accessed = current_date or datetime.now().strftime("%Y-%m-%d")
        self.access_count += 1

class EventStream:
    """同一事件链的记忆按时间排列"""
    def __init__(self, event_id: str, memories: List[Memory] = None, status: str = "open"):
        self.event_id = event_id
        self.status = status  # "open" | "closed"
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

    def peak_end_sample(self, k: int = 2) -> List[Memory]:
        """
        峰终采样（PROCESS 视图用）：
        - 起点（第一个节点）
        - 终点（最新节点）
        - status_update 非空的中间节点
        - salience top-K 节点
        返回 [起点, 关键转折1, 最高潮, 关键转折2, 终点]
        心理学依据：峰终定律 — 记忆由最巅峰时刻和最后结局决定
        """
        if not self.memories:
            return []
        if len(self.memories) <= k + 3:
            return self.memories[:]
        result = {}
        # 起点
        result[self.memories[0].memory_id] = self.memories[0]
        # 终点
        result[self.memories[-1].memory_id] = self.memories[-1]
        # status_update 非空的中间节点
        for m in self.memories[1:-1]:
            if m.status_update:
                result[m.memory_id] = m
        # salience top-K
        middle = sorted(
            [m for m in self.memories[1:-1] if m.memory_id not in result],
            key=lambda x: -x.salience
        )
        for m in middle[:k]:
            result[m.memory_id] = m
        return sorted(result.values(), key=lambda x: x.timestamp)

    def add(self, mem: Memory):
        mem.event_stream_id = self.event_id
        self.memories.append(mem)
        self.memories.sort(key=lambda m: m.timestamp)
