"""模块C：事件流索引 - 认知视图 / 回忆视图 / 混合视图"""
from typing import List
from core.memory import Memory, EventStream
from core.intent import STATUS, PROCESS, CHAT

def query_stream(
    stream: EventStream,
    intent: str,
    semantic_hit_ids: set = None,
) -> List[Memory]:
    """
    根据意图返回事件流的不同视图
    - STATUS: 认知视图（只取最新）
    - PROCESS: 回忆视图（全部）
    - CHAT: 混合视图（语义命中节点+最新背景）
    """
    if not stream.memories:
        return []

    if intent == STATUS:
        return [stream.latest()]

    if intent == PROCESS:
        return stream.all_versions()

    if intent == CHAT:
        result = []
        latest = stream.latest()
        seen = set()

        # 语义命中的过程节点保留
        if semantic_hit_ids:
            for mem in stream.memories:
                if mem.memory_id in semantic_hit_ids:
                    result.append(mem)
                    seen.add(mem.memory_id)

        # 最新状态作为背景
        if latest.memory_id not in seen:
            result.append(latest)

        return result

    return [stream.latest()]
