"""模块D：反思 — salience 累积触发 + LLM 洞察提炼

参考 Generative Agents 的 reflection 机制。
salience 驱动触发——越重要的事发生，反思越频繁。
"""

import time
from typing import List, Dict
from core.memory import Memory
from core.store import MemoryStore
from core.logger import get_logger

log = get_logger(__name__)

REFLECTION_THRESHOLD = 150
REFLECTION_QUESTIONS_PROMPT = """你是角色 {character_name}。回顾最近发生的这些事：

{recent_memories}

请站在角色视角，提出 3 个值得深思的高层问题。
只返回JSON数组：
["问题1", "问题2", "问题3"]
"""

REFLECTION_INSIGHT_PROMPT = """你是角色 {character_name}。思考这个问题：

{question}

相关记忆：
{related_memories}

请提炼 5 条洞察（每条1-2句话）。
只返回JSON数组：
["洞察1", "洞察2", "洞察3", "洞察4", "洞察5"]
"""


class ReflectionEngine:
    """反思引擎：累积显著性 → 触发反思 → LLM 提炼洞察"""

    def __init__(self, store: MemoryStore, character_name: str = "角色"):
        self._store = store
        self._character_name = character_name
        self._accumulator: float = 0.0
        self._last_reflection_count = len(store.list_all())

    def feed(self, salience: int):
        """
        每条新记忆入库后调用此方法。
        累积显著性，达到阈值则触发反思。
        """
        self._accumulator += salience
        if self._accumulator >= REFLECTION_THRESHOLD:
            self._trigger_reflection()
            self._accumulator = 0

    def _trigger_reflection(self):
        """触发反思流程"""
        new_memories = self._store.list_all()
        if len(new_memories) <= self._last_reflection_count:
            return

        # 取上次反思以来的新记忆
        recent = new_memories[self._last_reflection_count:]
        if len(recent) < 3:
            return

        log.info("reflection triggered: %d new memories, accumulator=%.0f",
                 len(recent), self._accumulator)

        # Step 1: 生成高层问题
        questions = self._generate_questions(recent)
        if not questions:
            return

        # Step 2: 对每个问题检索相关记忆并生成洞察
        for q in questions[:3]:
            related = self._find_related(q, recent)
            insights = self._generate_insights(q, related)
            if insights:
                self._store_insights(q, insights)

        self._last_reflection_count = len(new_memories)

    def _generate_questions(self, recent: List[Memory]) -> List[str]:
        """LLM 生成 3 个高层问题"""
        memories_text = "\n".join(
            f"- [{m.timestamp}] {m.content[:100]}" for m in recent[:20]
        )
        prompt = REFLECTION_QUESTIONS_PROMPT.format(
            character_name=self._character_name,
            recent_memories=memories_text,
        )
        raw = self._call_llm(prompt)
        parsed = self._parse_json_safe(raw)
        if isinstance(parsed, list):
            return parsed
        return []

    def _find_related(self, question: str, recent: List[Memory]) -> List[Memory]:
        """在近期记忆中找与问题语义相关的"""
        if len(recent) <= 5:
            return recent
        return recent[:10]  # 简化：取最近10条

    def _generate_insights(self, question: str, related: List[Memory]) -> List[str]:
        """LLM 生成 5 条洞察"""
        memories_text = "\n".join(
            f"- {m.content[:100]}" for m in related
        )
        prompt = REFLECTION_INSIGHT_PROMPT.format(
            character_name=self._character_name,
            question=question,
            related_memories=memories_text,
        )
        raw = self._call_llm(prompt)
        parsed = self._parse_json_safe(raw)
        if isinstance(parsed, list):
            return parsed
        return []

    def _store_insights(self, question: str, insights: List[str]):
        """洞察入库为 reflection 类型记忆"""
        import re
        for insight in insights[:5]:
            mem = Memory(
                memory_id=f"ref_{int(time.time()*1000)}_{len(self._store.list_all())}",
                content=f"[反思] {question}\n{insight}",
                timestamp=time.strftime("%Y-%m-%d"),
                event_stream_id="evt_reflection",
                lifecycle="active",
                salience=self._calc_reflection_salience(),
            )
            self._store.add(mem)

    def _calc_reflection_salience(self) -> int:
        """反思记忆继承证据节点的显著性"""
        return 5  # 默认，实际应从证据节点继承

    def _call_llm(self, prompt: str) -> str:
        from main import llm
        return llm(prompt, max_tokens=256)

    def _parse_json_safe(self, text: str):
        import json, re
        text = text.strip()
        m = re.search(r'\[.*\]', text, re.DOTALL)
        if m:
            text = m.group(0)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    @property
    def accumulator(self) -> float:
        return self._accumulator
