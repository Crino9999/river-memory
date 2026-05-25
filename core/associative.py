"""模块B：关联索引 - 物理坐标多路探针（返回命中来源）"""
from typing import List, Tuple, Set
from core.memory import Memory
from core.store import MemoryStore, embed_texts
from core.time_parser import date_score, is_overdue
from config import PROBE_WEIGHTS
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
    得分规则从 config.PROBE_WEIGHTS 读取，日期使用距离计分
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
        _add(mem.memory_id, PROBE_WEIGHTS["semantic"], "semantic")

    # 探针2: 时间（距离计分替代简单布尔）
    for mem in all_memories:
        ds = date_score(mem.timestamp, current_date)
        if ds > 0:
            _add(mem.memory_id, PROBE_WEIGHTS["time"] * ds // 3, "time")

    # 探针3: 对象（支持别名匹配）
    for mem in all_memories:
        if _objects_match(mem.objects, present_people):
            _add(mem.memory_id, PROBE_WEIGHTS["object"], "object")

    # 探针4: 环境
    for mem in all_memories:
        if mem.environment == current_env:
            _add(mem.memory_id, PROBE_WEIGHTS["env"], "env")

    # 合并排序
    result = [HitResult(
        memory=store.get_by_id(mid),
        score=scores[mid],
        sources=sources[mid],
    ) for mid in scores if scores[mid] > 0]
    result.sort(key=lambda x: -x.score)
    return result[:top_k]


ALIAS_MAP = {}  # 全局别名映射，可由外部配置


def _objects_match(mem_objects: List[str], present_people: List[str]) -> bool:
    """检查记忆对象是否与在场人物匹配（支持别名）"""
    if not mem_objects or not present_people:
        return False
    people_set = set(present_people)
    for p in present_people:
        people_set.update(ALIAS_MAP.get(p, []))
    for obj in mem_objects:
        if obj in people_set:
            return True
        obj_aliases = ALIAS_MAP.get(obj, [])
        if people_set & set(obj_aliases):
            return True
    return False


def set_alias_map(aliases: dict):
    """设置全局别名映射，如 {'A': ['老A', 'A哥'], '拉姆': ['蕾姆']}"""
    global ALIAS_MAP
    ALIAS_MAP = {k: list(v) for k, v in (aliases or {}).items()}

associative_search = search  # alias for backward compatibility
