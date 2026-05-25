"""River 记忆系统入口"""
import json, requests
from config import API_BASE, API_KEY, MODEL
from core.intent import classify, STATUS, PROCESS, CHAT
from core.associative import associative_search
from core.eventstream import query_stream
from core.memory import EventStream
from core.store import MemoryStore

def llm(prompt: str, max_tokens: int = 512) -> str:
    """调用LLM（OpenAI兼容接口）"""
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
    }, timeout=60)
    return resp.json()["choices"][0]["message"]["content"]

def recall(
    user_input: str,
    current_date: str,
    present_people: list,
    current_env: str,
    store: MemoryStore,
    character_name: str = "角色",
) -> str:
    """
    完整检索流程：
    1. 意图路由 → 确定STATUS/PROCESS/CHAT
    2. 关联索引 → 多路探针找记忆
    3. 事件流索引 → 按意图选视图
    4. LLM生成回复
    """
    intent = classify(user_input)
    hits = associative_search(user_input, current_date, present_people, current_env, store)
    print(f"  [意图] {intent}  [命中记忆数] {len(hits)}")

    if not hits:
        return llm(f"你是{character_name}。用户说：「{user_input}」。自然地回复。")

    # 按事件流分组（用 sources 字段判断语义命中）
    streams = {}
    semantic_ids = set()
    for hit in hits:
        mem = hit.memory
        if mem.event_stream_id not in streams:
            stream_mems = store.get_by_stream(mem.event_stream_id)
            streams[mem.event_stream_id] = EventStream(mem.event_stream_id, stream_mems)
        if "semantic" in hit.sources:
            semantic_ids.add(mem.memory_id)

    # 事件流索引 → 视图选择
    context_memories = []
    for sid, stream in streams.items():
        context_memories.extend(query_stream(stream, intent, semantic_ids))

    # 去重排序
    seen = set()
    final = []
    for mem in context_memories:
        if mem.memory_id not in seen:
            seen.add(mem.memory_id)
            final.append(mem)
    final.sort(key=lambda m: m.timestamp, reverse=True)

    # 构建上下文
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
    return llm(prompt)
