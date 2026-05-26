"""River 记忆系统入口"""
import json, time, requests
from typing import List, Set
from collections import deque
from config import API_BASE, API_KEY, MODEL, LLM_MAX_RETRIES, LLM_RETRY_DELAY, LLM_TIMEOUT, TOP_K
from core.intent import classify, STATUS, PROCESS, CHAT
from core.associative import associative_search, HitResult
from core.eventstream import query_stream
from core.memory import EventStream, Memory
from core.store import MemoryStore
from core.logger import get_logger

log = get_logger(__name__)

def llm(prompt: str, max_tokens: int = 512) -> str:
    last_err = None
    for attempt in range(LLM_MAX_RETRIES):
        try:
            if not API_KEY:
                return "[LLM未配置]"
            resp = requests.post(f"{API_BASE}/chat/completions", headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            }, json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.7,
            }, timeout=LLM_TIMEOUT)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            last_err = e
            log.warning("LLM call attempt %d/%d failed: %s", attempt + 1, LLM_MAX_RETRIES, e)
            if attempt < LLM_MAX_RETRIES - 1:
                time.sleep(LLM_RETRY_DELAY * (attempt + 1))
    log.error("LLM call failed after %d retries: %s", LLM_MAX_RETRIES, last_err)
    return "[LLM调用失败]"

def _propagate_related(
    hits: list,
    store: MemoryStore,
    top_k: int = 3,
    max_depth: int = 3,
    min_activation: float = 0.2,
) -> List[Memory]:
    """
    跨流因果激活传播：从命中记忆出发，沿 related_streams 按 link_strength
    衰减激活，拉取关联事件流的最新有效状态作为补充上下文。
    """
    visited_streams: Set[str] = set()
    result: List[Memory] = []

    for hit in hits[:top_k]:
        mem = hit.memory
        if not mem.related_streams:
            continue

        base_activation = 1.5 if mem.salience >= 8 else 1.0

        queue = deque()
        for i, rsid in enumerate(mem.related_streams):
            if rsid in visited_streams:
                continue
            link = mem.link_strength[i] if i < len(mem.link_strength) else 0.5
            activation = base_activation * link
            queue.append((rsid, activation, 1))

        while queue:
            rsid, activation, depth = queue.popleft()
            if activation < min_activation or depth > max_depth:
                continue
            if rsid in visited_streams:
                continue
            visited_streams.add(rsid)

            stream_mems = store.get_by_stream(rsid)
            if stream_mems:
                stream = EventStream(rsid, stream_mems)
                state = stream.current_state()
                if state:
                    result.append(state)

            # 继续传播到下一层
            if stream_mems:
                for m in stream_mems:
                    if m.related_streams:
                        for i, next_rsid in enumerate(m.related_streams):
                            next_link = m.link_strength[i] if i < len(m.link_strength) else 0.5
                            next_activation = activation * next_link
                            queue.append((next_rsid, next_activation, depth + 1))

    return result

def recall(
    user_input: str,
    current_date: str,
    present_people: list,
    current_env: str,
    store: MemoryStore,
    character_name: str = "角色",
) -> dict:
    """
    完整检索流程：
    1. 意图路由 → 确定STATUS/PROCESS/CHAT
    2. 关联索引 → 多路探针找记忆
    3. 事件流索引 → 按意图选视图
    4. LLM生成回复

    返回 dict: {response, intent, hits, memory_context}
    """
    log.info("recall input='%s' date=%s people=%s env=%s", user_input[:50], current_date, present_people, current_env)

    intent = classify(user_input)
    hits = associative_search(user_input, current_date, present_people, current_env, store, top_k=TOP_K)
    log.info("intent=%s hits=%d", intent, len(hits))

    if not hits:
        prompt = f"你是{character_name}。用户说：「{user_input}」。自然地回复。"
        log.info("recall: no hits, direct chat")
        return {
            "response": llm(prompt),
            "intent": intent,
            "hits": [],
            "memory_context": [],
        }

    streams = {}
    semantic_ids = set()
    for hit in hits:
        mem = hit.memory
        if mem.event_stream_id not in streams:
            try:
                stream_mems = store.get_by_stream(mem.event_stream_id)
                if stream_mems:
                    streams[mem.event_stream_id] = EventStream(mem.event_stream_id, stream_mems)
            except Exception as e:
                log.warning("Failed to load stream %s: %s", mem.event_stream_id, e)
        if "semantic" in hit.sources:
            semantic_ids.add(mem.memory_id)

    context_memories = []
    for sid, stream in streams.items():
        context_memories.extend(query_stream(stream, intent, semantic_ids))

    seen = set()
    final = []
    for mem in context_memories:
        if mem.memory_id not in seen:
            seen.add(mem.memory_id)
            final.append(mem)
    final.sort(key=lambda m: m.timestamp, reverse=True)

    # === FSRS 检索后强化 ===
    for hit in hits:
        hit.memory.reinforce(current_date)
        store.reinforce_memory(
            hit.memory.memory_id, hit.memory.stability,
            hit.memory.last_accessed, hit.memory.access_count,
        )

    # === 跨流因果激活传播 ===
    related_context = _propagate_related(hits, store, top_k=TOP_K)
    if related_context:
        for m in related_context:
            if m.memory_id not in seen:
                seen.add(m.memory_id)
                final.append(m)

    final.sort(key=lambda m: m.timestamp, reverse=True)
    log.info("recall: %d final (incl %d related)", len(final), len(related_context))

    context = "\n".join(
        f"- [{m.timestamp}] {m.content}{' [状态: '+m.status_update+']' if m.status_update else ''}"
        for m in final
    )
    prompt = (
        f"你是一个拥有记忆的角色。今天是{current_date}。\n"
        f"相关记忆:\n{context}\n\n"
        f"当前情况: 用户「{user_input}」\n"
        f"请自然地回复，融入记忆中的信息。\n"
    )
    return {
        "response": llm(prompt),
        "intent": intent,
        "hits": hits,
        "memory_context": final,
    }
