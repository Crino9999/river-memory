"""记忆自动入库：物理坐标提取 + 事件流归属判定 + MemoryIngestor"""
import json, time, uuid, re
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from core.memory import Memory
from core.store import MemoryStore
from core.logger import get_logger
from config import DEDUP_WINDOW_DAYS

log = get_logger(__name__)

EXTRACT_COORDINATES_PROMPT = """你是一个记忆提取助手。分析以下对话，提取物理坐标信息。

【用户消息】{user_msg}

【角色回复】{bot_reply}

【当前场景】日期: {current_date}，在场人物: {present_people}，当前环境: {current_env}

请提取并返回JSON（只返回JSON，不要其他文字）：
{{
  "content": "角色对这段对话的第一人称记忆总结（1-2句话，用角色视角写）",
  "objects": ["在场/提及的人物名"],
  "environment": "当前环境（如果对话暗示环境变化，更新为新环境）",
  "status_update": "如果有状态变化或承诺（如欠钱、治伤、约定），写成 '主语=新状态' 格式。无变化则为 null"
}}

规则：
1. objects 提取所有被提到/在场的人物（不包括"我"和泛指）
2. environment 如果能从对话推断 (如"我们回家吧"→"家") 则更新，否则用 current_env
3. 如果对话无新信息（纯寒暄），content 写 "日常闲聊"，status_update 为 null
4. 对话隐含信息要推断：如"还钱的事再说"意味着存在债务关系
"""

STREAM_AFFILIATE_PROMPT = """你是一个事件流归类助手。判断新记忆属于哪个已有事件流，或者需要新建。

【新记忆内容】{content}

【新记忆物理坐标】日期: {timestamp}，对象: {objects}，环境: {environment}

【已有事件流】（每个流显示ID和最近记忆摘要）
{streams_summary}

请判断归属并返回JSON（只返回JSON）：
{{"stream_id": "流ID", "confidence": 0.0-1.0}}

规则：
- 如果新记忆和某个已有流明显相关（同一事件/同一话题），返回该流ID，confidence>0.7
- 如果属于新事件，返回 {{"stream_id": "new:推荐流名", "confidence": 0.9}}
- 流名用英文slug，如 evt_debt_A, evt_heal_horn
- 模糊匹配时 (0.4-0.7) 也归入已有流，但 confidence 较低
- 完全无关时 (confidence<0.4) 新建流
"""


def _call_llm(prompt: str, max_tokens: int = 512) -> str:
    from main import llm
    return llm(prompt, max_tokens)

def _llm_not_available(raw: str) -> bool:
    return raw.startswith("[LLM") or raw.startswith("#[LLM")


def _parse_json_safe(text: str) -> Optional[dict]:
    text = text.strip()
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        text = m.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            text = text.replace("'", '"')
            text = re.sub(r'(\w+):', r'"\1":', text)
            return json.loads(text)
        except (json.JSONDecodeError, Exception):
            return None


def extract_coordinates(
    user_msg: str,
    bot_reply: str,
    current_date: str,
    present_people: List[str],
    current_env: str,
) -> dict:
    """
    从对话中提取物理坐标：content, objects, environment, status_update
    返回 dict 含 content / objects / environment / status_update
    """
    prompt = EXTRACT_COORDINATES_PROMPT.format(
        user_msg=user_msg,
        bot_reply=bot_reply,
        current_date=current_date,
        present_people=", ".join(present_people) if present_people else "无",
        current_env=current_env or "未知",
    )

    for attempt in range(3):
        try:
            raw = _call_llm(prompt, max_tokens=512)
            if _llm_not_available(raw):
                break
            data = _parse_json_safe(raw)
            if data:
                return {
                    "content": data.get("content", "日常闲聊"),
                    "objects": data.get("objects", []) or [],
                    "environment": data.get("environment") or current_env,
                    "status_update": data.get("status_update"),
                }
            log.warning("extract_coordinates: JSON parse failed, attempt %d, raw=%s", attempt + 1, raw[:100])
        except Exception as e:
            log.warning("extract_coordinates: LLM error attempt %d: %s", attempt + 1, e)
        time.sleep(0.5)

    return {
        "content": f"用户说「{user_msg[:50]}」，角色回复了。",
        "objects": [],
        "environment": current_env,
        "status_update": None,
    }


def classify_stream(
    content: str,
    timestamp: str,
    objects: List[str],
    environment: str,
    existing_streams: List[Tuple[str, str]],
) -> Tuple[str, float]:
    """
    判断新记忆属于哪个事件流
    existing_streams: [(stream_id, latest_memory_summary), ...]
    返回 (stream_id, confidence)
    """
    if not existing_streams:
        new_id = _generate_stream_id(content, objects)
        log.info("classify_stream: no existing streams, creating new=%s", new_id)
        return f"new:{new_id}", 0.9

    summary = "\n".join(
        f"  - ID: {sid} | 最近记忆: {snippet[:80]}"
        for sid, snippet in existing_streams
    )

    prompt = STREAM_AFFILIATE_PROMPT.format(
        content=content,
        timestamp=timestamp,
        objects=", ".join(objects) if objects else "无",
        environment=environment,
        streams_summary=summary,
    )

    for attempt in range(3):
        try:
            raw = _call_llm(prompt, max_tokens=256)
            if _llm_not_available(raw):
                break
            data = _parse_json_safe(raw)
            if data:
                sid = data.get("stream_id", "")
                conf = float(data.get("confidence", 0.5))
                if sid.startswith("new:"):
                    stream_name = sid[4:].strip() or _generate_stream_id(content, objects)
                    return f"new:{stream_name}", conf
                return sid, conf
            log.warning("classify_stream: JSON parse failed, attempt %d", attempt + 1)
        except Exception as e:
            log.warning("classify_stream: LLM error attempt %d: %s", attempt + 1, e)
        time.sleep(0.5)

    new_id = _generate_stream_id(content, objects)
    log.warning("classify_stream: fallback to new stream %s", new_id)
    return f"new:{new_id}", 0.3


def _generate_stream_id(content: str, objects: List[str]) -> str:
    obj_part = "_".join(objects[:2]) if objects else "generic"
    obj_part = re.sub(r'[^a-zA-Z0-9_-]', '', obj_part)
    ts = datetime.now().strftime("%y%m%d%H%M%S")
    return f"evt_{obj_part}_{ts}"


class MemoryIngestor:
    """记忆自动入库管理器"""

    def __init__(self, store: MemoryStore):
        self._store = store
        self._id_counter = int(datetime.now().timestamp() * 1000)

    def _next_id(self) -> str:
        self._id_counter += 1
        return f"mem_{self._id_counter}"

    def _get_existing_streams(self) -> List[Tuple[str, str]]:
        all_mems = self._store.list_all()
        streams: Dict[str, List[Memory]] = {}
        for m in all_mems:
            if m.event_stream_id:
                streams.setdefault(m.event_stream_id, []).append(m)
        result = []
        for sid, mems in streams.items():
            mems.sort(key=lambda x: x.timestamp)
            latest = mems[-1]
            result.append((sid, latest.content[:80]))
        return result

    def _is_duplicate(self, content: str, timestamp: str) -> bool:
        all_mems = self._store.list_all()
        for m in all_mems:
            if m.timestamp == timestamp and content[:30] == m.content[:30]:
                log.info("dedup: skipping duplicate memory '%s'", content[:50])
                return True
        try:
            target_date = datetime.strptime(timestamp, "%Y-%m-%d")
            for m in all_mems:
                try:
                    m_date = datetime.strptime(m.timestamp, "%Y-%m-%d")
                    if abs((target_date - m_date).days) <= DEDUP_WINDOW_DAYS:
                        if content[:30] == m.content[:30]:
                            return True
                except ValueError:
                    continue
        except ValueError:
            pass
        return False

    def ingest(
        self,
        user_msg: str,
        bot_reply: str,
        current_date: str,
        present_people: List[str] = None,
        current_env: str = "",
    ) -> List[Memory]:
        """
        完整入库流程：提取坐标 → 判定事件流 → 写入存储
        返回新增的 Memory 列表
        """
        present_people = present_people or []

        log.info("ingest: start user='%s' date=%s", user_msg[:50], current_date)

        coords = extract_coordinates(
            user_msg, bot_reply, current_date, present_people, current_env
        )

        content = coords["content"]
        objects = coords["objects"]
        environment = coords["environment"]
        status_update = coords.get("status_update")

        log.info("ingest: extracted content='%s' objects=%s env=%s status=%s",
                 content[:60], objects, environment, status_update)

        if self._is_duplicate(content, current_date):
            log.info("ingest: duplicate detected, skipping")
            return []

        existing_streams = self._get_existing_streams()
        stream_id, confidence = classify_stream(
            content, current_date, objects, environment, existing_streams
        )

        if stream_id.startswith("new:"):
            stream_id = stream_id[4:]
            log.info("ingest: created new stream %s (confidence=%.2f)", stream_id, confidence)
        else:
            log.info("ingest: affiliated to stream %s (confidence=%.2f)", stream_id, confidence)

        mem = Memory(
            memory_id=self._next_id(),
            content=content,
            timestamp=current_date,
            event_stream_id=stream_id,
            objects=objects,
            environment=environment,
            status_update=status_update,
            confidence=confidence,
            lifecycle="active",
        )

        if confidence < 0.5:
            log.warning("ingest: low confidence %.2f for '%s', consider review", confidence, content[:60])

        try:
            self._store.add(mem)
            log.info("ingest: stored memory %s to stream %s (conf=%.2f)", mem.memory_id, stream_id, confidence)
        except Exception as e:
            log.error("ingest: store error for %s: %s", mem.memory_id, e)
            return []

        return [mem]


def create_ingestor(store: MemoryStore) -> MemoryIngestor:
    return MemoryIngestor(store)
