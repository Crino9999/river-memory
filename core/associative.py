"""模块B：关联索引 - 物理坐标多路探针"""
from typing import List, Tuple
from core.memory import Memory
from core.store import MemoryStore, embed_texts

def search(
    user_input: str,
    current_date: str,
    present_people: List[str],
    current_env: str,
    store: MemoryStore,
    top_k: int = 5,
) -> List[Tuple[Memory, int]]:
    """
    多路探针并行检索，返回 (Memory, 得分) 列表，按分降序
    得分规则: 语义+1, 时间+2, 对象+3, 环境+1
    """
    all_memories = store.list_all()
    scores: dict = {}

    # 探针1: 语义
    query_emb = embed_texts([user_input])[0]
    semantic_results = store.vector_search(query_emb, top_k)
    for mem, _ in semantic_results:
        scores[mem.memory_id] = scores.get(mem.memory_id, 0) + 1

    # 探针2: 时间
    for mem in all_memories:
        if _date_related(mem.timestamp, current_date):
            scores[mem.memory_id] = scores.get(mem.memory_id, 0) + 2

    # 探针3: 对象
    for mem in all_memories:
        if set(mem.objects) & set(present_people):
            scores[mem.memory_id] = scores.get(mem.memory_id, 0) + 3

    # 探针4: 环境
    for mem in all_memories:
        if mem.environment == current_env:
            scores[mem.memory_id] = scores.get(mem.memory_id, 0) + 1

    # 合并排序
    result = [(store.get_by_id(mid), s) for mid, s in scores.items() if s > 0]
    result.sort(key=lambda x: -x[1])
    return result[:top_k]

def _date_related(timestamp: str, current_date: str) -> bool:
    """检查记忆的时间坐标是否与当前日期相关"""
    if not timestamp:
        return False
    return timestamp == current_date or timestamp <= current_date

# 别名，与test_demo.py和main.py中的导入兼容
associative_search = search
