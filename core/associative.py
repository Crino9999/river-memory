"""模块B：关联索引 - 物理坐标多路探针（返回命中来源）"""
from typing import List, Tuple, Set
from core.memory import Memory
from core.store import MemoryStore, embed_texts
from dataclasses import dataclass, field

@dataclass
class HitResult:
    memory: Memory
    score: int
    sources: Set[str] = field(default_factory=set)  # {"semantic","time","object","env"}

def search(
    user_input: str,
    current_date: str,
    present_people: List[str],
    current_env: str,
    store: MemoryStore,
    top_k: int = 5,
) -> List[HitResult]:
    """
    多路探针并行检索，返回 HitResult 列表（含命中来源），按分降序
    得分规则: 语义+1, 时间+2, 对象+3, 环境+1
    """
    all_memories = store.list_all()
    scores: dict = {}
    sources: dict = {}

    def _add(mid, pts, src):
        scores[mid] = scores.get(mid, 0) + pts
        if mid not in sources:
            sources[mid] = set()
        sources[mid].add(src)

    # 探针1: 语义
    query_emb = embed_texts([user_input])[0]
    semantic_results = store.vector_search(query_emb, top_k)
    for mem, _ in semantic_results:
        _add(mem.memory_id, 1, "semantic")

    # 探针2: 时间
    for mem in all_memories:
        if _date_related(mem.timestamp, current_date):
            _add(mem.memory_id, 2, "time")

    # 探针3: 对象
    for mem in all_memories:
        if set(mem.objects) & set(present_people):
            _add(mem.memory_id, 3, "object")

    # 探针4: 环境
    for mem in all_memories:
        if mem.environment == current_env:
            _add(mem.memory_id, 1, "env")

    # 合并排序
    result = [HitResult(
        memory=store.get_by_id(mid),
        score=scores[mid],
        sources=sources[mid],
    ) for mid in scores if scores[mid] > 0]
    result.sort(key=lambda x: -x.score)
    return result[:top_k]

def _date_related(timestamp: str, current_date: str) -> bool:
    if not timestamp:
        return False
    return timestamp == current_date or timestamp <= current_date
