"""模块B：关联索引 - 物理坐标多路探针 + v3 三因子评分"""
from typing import List, Tuple, Set, Dict
from core.memory import Memory
from core.store import MemoryStore, embed_texts
from core.time_parser import date_score, is_overdue
from dataclasses import dataclass, field

@dataclass
class HitResult:
    memory: Memory
    score: float
    sources: Set[str] = field(default_factory=set)

def search(
    user_input: str,
    current_date: str,
    present_people: List[str],
    current_env: str,
    store: MemoryStore,
    top_k: int = 5,
) -> List[HitResult]:
    """
    v3 三因子评分检索：
    1. 过度拉取候选池 (top_k × 5)
    2. 对每条候选计算：语义相关性 + salience/10 + retrieval_strength
    3. 物理坐标多命中 (>=2根) 置顶
    4. 截取 top_k
    """
    all_memories = store.list_all()
    if not all_memories:
        return []

    candidate_ids: Dict[str, float] = {}  # mid → semantic_sim
    sources: Dict[str, Set[str]] = {}

    def _add_candidate(mid, sim, src):
        if mid not in candidate_ids or sim > candidate_ids[mid]:
            candidate_ids[mid] = sim
        sources.setdefault(mid, set()).add(src)

    # === 语义探针：过度拉取 ===
    query_emb = embed_texts([user_input])[0]
    semantic_results = store.vector_search(query_emb, top_k * 5)
    for mem, sim in semantic_results:
        _add_candidate(mem.memory_id, sim, "semantic")

    # === 物理坐标探针：标记命中来源 + 加入候选池 ===
    for mem in all_memories:
        got_any = False

        # 时间探针
        ds = date_score(mem.timestamp, current_date, lifecycle=mem.lifecycle)
        if ds > 0:
            _add_candidate(mem.memory_id, 0.0, "time")
            got_any = True

        # 对象探针
        if _objects_match(mem.objects, present_people):
            _add_candidate(mem.memory_id, 0.0, "object")
            got_any = True

        # 环境探针
        if mem.environment and mem.environment == current_env:
            _add_candidate(mem.memory_id, 0.0, "env")
            got_any = True

    # === 三因子评分 ===
    scored: List[Tuple[HitResult, int]] = []  # (HitResult, multi_hit_count)
    for mid, semantic_sim in candidate_ids.items():
        mem = store.get_by_id(mid)
        if not mem:
            continue

        importance = mem.salience / 10.0
        retrieval = mem.retrieval_strength

        total = semantic_sim + importance + retrieval

        # 多坐标命中计数
        physical_hits = len(sources.get(mid, set()) & {"time", "object", "env"})
        multi_hit = 1 if physical_hits >= 2 else 0

        scored.append((
            HitResult(memory=mem, score=round(total, 4), sources=sources.get(mid, set())),
            multi_hit,
        ))

    # 排序：多命中置顶 → 总分降序
    scored.sort(key=lambda x: (-x[1], -x[0].score))

    return [h for h, _ in scored[:top_k]]


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
