"""记忆自动入库：物理坐标提取 + 事件流归属判定 + MemoryIngestor"""
import json, time, uuid, re
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from core.memory import Memory
from core.store import MemoryStore
from core.logger import get_logger
from config import DEDUP_WINDOW_DAYS

log = get_logger(__name__)

EXTRACT_COORDINATES_PROMPT = """你是一个记忆提取助手。分析以下对话，提取物理坐标和记忆元数据。

【用户消息】{user_msg}

【角色回复】{bot_reply}

【当前场景】日期: {current_date}，在场人物: {present_people}，当前环境: {current_env}

【已有事件流】（每个流显示ID和最近记忆摘要）
{existing_streams}

请提取并返回JSON（只返回JSON，不要其他文字）：
{{
  "content": "角色对这段对话的第一人称记忆总结（1-2句话）",
  "objects": ["在场/提及的人物名"],
  "environment": "对话暗示的环境（无变化则用当前环境）",
  "status_update": "如果有状态变化或承诺，写成 '主语=新状态'。无则为 null",
  "salience": {{
    "emotional": 1-10,
    "relational": 1-10,
    "narrative": 1-10
  }},
  "volatility": "low/medium/high",
  "lifecycle_state": "pending/active/resolved/dream",
  "related_streams": ["关联事件流ID（如果有因果关系）"],
  "link_strength": [0.3-0.9],
  "reinterpretation": {{"target_memory_id": "旧记忆ID", "new_understanding": "事后解读"}},
  "correction": {{"target_memory_id": "旧记忆ID", "old_fact": "错误内容", "new_fact": "正确内容"}}
}}

评分规则：
1. salience 三维度：
   - emotional: 情感冲击力 1=平淡 10=山崩地裂
   - relational: 对角色间关系的影响 1=无 10=永久改变
   - narrative: 对剧情走向的改变 1=日常流水 10=世界观颠覆
2. volatility（情感易逝性）：
   - low=刻骨铭心（创伤、永久关系改变、死亡）
   - high=一时冲动（争吵后和好、单次激情）
   - medium=其他
3. lifecycle_state：待完成承诺→pending，已完成→resolved，正常→active，梦境/玩笑→dream
4. related_streams：与其他事件流存在因果关系时才填写（如"骨折是因被推倒导致的"），没有就空数组
5. reinterpretation：如果本对话重新解读了某条旧记忆（情感色彩变了但事实不变），需填写
6. correction：如果本对话更正了某条旧记忆的事实错误（内容本身就是错的），需填写
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
    existing_streams: List[Tuple[str, str]] = None,
) -> dict:
    """
    从对话中提取物理坐标 + v3 元数据。
    返回 dict 含 content/objects/environment/status_update/
                salience/volatility/lifecycle_state/related_streams/link_strength/
                reinterpretation/correction
    """
    streams_text = ""
    if existing_streams:
        streams_text = "\n".join(
            f"  - ID: {sid} | 摘要: {snippet[:80]}"
            for sid, snippet in existing_streams
        )

    prompt = EXTRACT_COORDINATES_PROMPT.format(
        user_msg=user_msg,
        bot_reply=bot_reply,
        current_date=current_date,
        present_people=", ".join(present_people) if present_people else "无",
        current_env=current_env or "未知",
        existing_streams=streams_text or "无已有事件流",
    )

    for attempt in range(3):
        try:
            raw = _call_llm(prompt, max_tokens=512)
            if _llm_not_available(raw):
                break
            data = _parse_json_safe(raw)
            if data:
                # 计算综合 salience
                s = data.get("salience", {})
                if isinstance(s, dict):
                    em, rl, na = s.get("emotional", 5), s.get("relational", 5), s.get("narrative", 5)
                    salience_score = max(em, rl, na) * 0.7 + (em + rl + na) / 3 * 0.3
                else:
                    salience_score = int(s) if s else 5

                return {
                    "content": data.get("content", "日常闲聊"),
                    "objects": data.get("objects", []) or [],
                    "environment": data.get("environment") or current_env,
                    "status_update": data.get("status_update"),
                    "salience": round(salience_score),
                    "volatility": data.get("volatility", "medium"),
                    "lifecycle_state": data.get("lifecycle_state", "active"),
                    "related_streams": data.get("related_streams", []) or [],
                    "link_strength": data.get("link_strength", []) or [],
                    "reinterpretation": data.get("reinterpretation"),
                    "correction": data.get("correction"),
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
        "salience": 5,
        "volatility": "medium",
        "lifecycle_state": "active",
        "related_streams": [],
        "link_strength": [],
        "reinterpretation": None,
        "correction": None,
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


def _fsrs_init(salience: int, volatility: str) -> Tuple[float, float]:
    """
    FSRS 初始 stability 和 difficulty。
    stability: 由 salience + volatility 共同决定
    difficulty: D_init = 10 - salience
    """
    if salience >= 9:
        stability = 9.0 if volatility == "low" else 2.0
    elif salience >= 7:
        stability = 8.3 if volatility == "low" else 2.0 if volatility == "high" else 4.5
    elif salience >= 4:
        stability = 4.5 if volatility == "low" else 1.5 if volatility == "high" else 2.3
    else:
        stability = 1.0

    difficulty = max(1.0, 10 - salience)
    return stability, difficulty


class MemoryIngestor:
    """记忆自动入库管理器"""

    def __init__(self, store: MemoryStore, reflector=None, guard=None):
        self._store = store
        self._reflector = reflector
        self._guard = guard
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
            user_msg, bot_reply, current_date, present_people, current_env,
            existing_streams=self._get_existing_streams(),
        )

        content = coords["content"]
        objects = coords["objects"]
        environment = coords["environment"]
        status_update = coords.get("status_update")
        salience = coords.get("salience", 5)
        volatility = coords.get("volatility", "medium")
        lifecycle_from_llm = coords.get("lifecycle_state", "active")
        related_streams = coords.get("related_streams", [])
        link_strength = coords.get("link_strength", [])
        reinterpretation = coords.get("reinterpretation")
        correction = coords.get("correction")

        log.info("ingest: extracted content='%s' objects=%s env=%s status=%s salience=%d vol=%s",
                 content[:60], objects, environment, status_update, salience, volatility)

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

        # === FSRS 初始化 ===
        stability_init, difficulty_init = _fsrs_init(salience, volatility)

        # === 一致性校验 ===
        guard_warnings = []
        guard_ok = True
        actual_lifecycle = lifecycle_from_llm

        if self._guard:
            from core.consistency import CheckReport
            report = self._guard.validate(
                coords, present_people, current_env, stream_id
            )
            guard_warnings = report.warnings + report.blocks
            guard_ok = report.passed
            if report.recommended_lifecycle:
                actual_lifecycle = report.recommended_lifecycle
            if not guard_ok:
                log.warning("guard blocked: %s", report.blocks[:3])
            elif guard_warnings:
                log.info("guard warnings: %s", guard_warnings[:3])

        mem = Memory(
            memory_id=self._next_id(),
            content=content,
            timestamp=current_date,
            event_stream_id=stream_id,
            objects=objects,
            environment=environment,
            status_update=status_update,
            confidence=confidence,
            lifecycle=actual_lifecycle,
            salience=salience,
            volatility=volatility,
            stability=stability_init,
            difficulty=difficulty_init,
            related_streams=related_streams,
            link_strength=link_strength,
            provenance_context=f"日期{current_date}，人物{objects}，环境{environment}",
        )

        if confidence < 0.5:
            log.warning("ingest: low confidence %.2f for '%s', queued for review", confidence, content[:60])
            try:
                self._store.add_review(mem.memory_id, content, confidence, "low confidence stream affiliation")
            except Exception as e:
                log.error("Failed to add review item: %s", e)

        if not guard_ok:
            try:
                reasons = "; ".join(guard_warnings[:3])
                self._store.add_review(mem.memory_id, content, confidence,
                                       f"consistency block: {reasons}")
            except Exception as e:
                log.error("Failed to add guard review item: %s", e)
        elif guard_warnings:
            try:
                reasons = "; ".join(guard_warnings[:3])
                self._store.add_review(mem.memory_id, content, confidence,
                                       f"consistency warn: {reasons}")
            except Exception as e:
                log.error("Failed to add guard warning item: %s", e)

        try:
            self._store.add(mem)
            log.info("ingest: stored memory %s to stream %s (conf=%.2f salience=%d)",
                     mem.memory_id, stream_id, confidence, salience)
        except Exception as e:
            log.error("ingest: store error for %s: %s", mem.memory_id, e)
            return []

        # === 再巩固处理 ===
        if reinterpretation:
            target_id = reinterpretation.get("target_memory_id")
            if target_id:
                target = self._store.get_by_id(target_id)
                if target:
                    target.reinterpretation = reinterpretation.get("new_understanding", "")
                    self._store.add(target)
                    log.info("reinterpretation: updated %s", target_id)

        if correction:
            target_id = correction.get("target_memory_id")
            if target_id:
                target = self._store.get_by_id(target_id)
                if target:
                    entry = {
                        "old": correction.get("old_fact", ""),
                        "new": correction.get("new_fact", ""),
                        "corrected_at": current_date,
                    }
                    target.correction_history = (target.correction_history or []) + [entry]
                    self._store.add(target)
                    log.info("correction: updated %s", target_id)

        # === 反思引擎喂入 ===
        if self._reflector:
            self._reflector.feed(salience)

        return [mem]

    def resolve_stream(self, event_stream_id: str, resolution: str = "resolved",
                       status_update: str = None):
        """
        标记一个事件流为已完成：
        - lifecycle → resolved
        - 可选：追加一条 status_update
        """
        from core.memory import RESOLVED, SUPERSEDED
        state = RESOLVED if resolution == "resolved" else SUPERSEDED
        self._store.set_stream_lifecycle(event_stream_id, state)
        if status_update:
            mem = Memory(
                memory_id=self._next_id(),
                content=f"[系统] 事件流 {event_stream_id} 已{resolution}: {status_update}",
                timestamp=datetime.now().strftime("%Y-%m-%d"),
                event_stream_id=event_stream_id,
                status_update=status_update,
                lifecycle=state,
            )
            self._store.add(mem)
        log.info("resolve_stream: %s → %s", event_stream_id, state)

    def cancel_promise(self, memory_id: str, reason: str = ""):
        """
        取消一个未完成的承诺：
        - 将该记忆所在整个事件流的 lifecycle → invalid
        """
        from core.memory import INVALID
        mem = self._store.get_by_id(memory_id)
        if mem:
            if reason and mem.event_stream_id:
                self._store.set_stream_lifecycle(mem.event_stream_id, INVALID)
            self._store.set_lifecycle(memory_id, INVALID)
            log.info("cancel_promise: %s → invalid (%s)", memory_id, reason)

    def mark_as_dream(self, memory_id: str):
        """标记一条记忆为梦境（非真实发生）"""
        from core.memory import DREAM
        self._store.set_lifecycle(memory_id, DREAM)
        log.info("mark_as_dream: %s", memory_id)

    def merge_to_stream(self, memory_id: str, target_stream_id: str):
        """将一条记忆重新归入另一个事件流（误归流修正）"""
        mem = self._store.get_by_id(memory_id)
        if mem:
            old_stream = mem.event_stream_id
            mem.event_stream_id = target_stream_id
            self._store.add(mem)
            log.info("merge_to_stream: %s moved from %s → %s", memory_id, old_stream, target_stream_id)


def create_ingestor(store: MemoryStore) -> MemoryIngestor:
    return MemoryIngestor(store)
