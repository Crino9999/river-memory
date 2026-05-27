"""一致性校验层：入库前的 Schema 校验 + 逻辑矛盾检测 + 幻觉标记

三道安检：
  1. Schema 校验 — 必填字段、类型、值域
  2. 生命周期一致性 — 状态流转合规、矛盾检测
  3. 幻觉检测 — 内容与已知世界状态冲突

所有检测不通过的记忆不会阻止入库，但会被标记 lifecycle=review_needed
并附带 fail_reasons，后续由审查队列处理。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set, Tuple
from core.memory import Memory, EventStream, PENDING, ACTIVE, RESOLVED, SUPERSEDED, INVALID, DREAM
from core.store import MemoryStore
from core.logger import get_logger

log = get_logger(__name__)

VALID_LIFECYCLES = {PENDING, ACTIVE, RESOLVED, SUPERSEDED, INVALID, DREAM}
TERMINAL_LIFECYCLES = {RESOLVED, INVALID, SUPERSEDED}


@dataclass
class CheckReport:
    passed: bool = True
    warnings: List[str] = field(default_factory=list)
    blocks: List[str] = field(default_factory=list)
    recommended_lifecycle: Optional[str] = None


class ConsistencyGuard:
    """入库前一致性校验器"""

    def __init__(self, store: MemoryStore):
        self._store = store

    def validate(self, coords: dict, present_people: List[str],
                 current_env: str, stream_id: str = None) -> CheckReport:
        """
        对即将入库的协调数据做全量校验。
        返回 CheckReport，passed=False 时记忆仍可入库但应标记待审。
        """
        report = CheckReport()

        self._check_schema(coords, report)
        if stream_id:
            self._check_lifecycle_consistency(coords, stream_id, report)
            self._check_contradiction(coords, stream_id, report)
        self._check_hallucination(coords, present_people, current_env, report)

        return report

    # ======== 第一道：Schema 校验 ========

    def _check_schema(self, coords: dict, report: CheckReport):
        content = coords.get("content", "")
        if not content or not isinstance(content, str) or len(content) < 3:
            report.blocks.append("content 为空或过短（<3字符）")
            report.passed = False

        salience = coords.get("salience", 5)
        if not isinstance(salience, (int, float)) or salience < 1 or salience > 10:
            report.warnings.append(f"salience 超出范围 [1,10]: {salience}")
            coords["salience"] = max(1, min(10, int(salience or 5)))

        lifecycle = coords.get("lifecycle_state", "active")
        if lifecycle not in VALID_LIFECYCLES:
            report.warnings.append(f"lifecycle 非法值 '{lifecycle}'，回退为 active")
            coords["lifecycle_state"] = "active"

        volatility = coords.get("volatility", "medium")
        if volatility not in ("low", "medium", "high"):
            report.warnings.append(f"volatility 非法值 '{volatility}'，回退为 medium")
            coords["volatility"] = "medium"

        objects = coords.get("objects", [])
        if not isinstance(objects, list):
            report.warnings.append("objects 不是 list，置为空")
            coords["objects"] = []

    # ======== 第二道：生命周期一致性 ========

    def _check_lifecycle_consistency(self, coords: dict, stream_id: str,
                                     report: CheckReport):
        existing = self._store.get_by_stream(stream_id)
        if not existing:
            return

        stream = EventStream(stream_id, existing)
        current = stream.current_state()
        if not current:
            return

        new_lifecycle = coords.get("lifecycle_state", "active")

        # 已终结的流不应再接收新的 pending/active 状态
        if current.lifecycle in TERMINAL_LIFECYCLES:
            if new_lifecycle in (PENDING, ACTIVE):
                report.warnings.append(
                    f"事件流 {stream_id} 已终结({current.lifecycle})，"
                    f"但新记忆宣告为 {new_lifecycle}。"
                    f"旧状态: {current.content[:50]} | 新内容: {coords.get('content', '')[:50]}"
                )
                report.recommended_lifecycle = current.lifecycle

        # resolved → pending 回退必须显式声明
        if current.lifecycle == RESOLVED and new_lifecycle == PENDING:
            report.blocks.append(
                f"事件流 {stream_id} 已 RESOLVED，新记忆试图回退到 PENDING。"
                f"如果是状态复发，请显式更新 status_update"
            )
            report.passed = False

    # ======== 第二道：状态矛盾检测 ========

    def _check_contradiction(self, coords: dict, stream_id: str,
                             report: CheckReport):
        new_status = coords.get("status_update")
        if not new_status:
            return

        existing = self._store.get_by_stream(stream_id)
        if not existing:
            return

        # 提取所有已存在的 status_update
        known_statuses: Dict[str, str] = {}  # key → value
        for m in existing:
            if m.status_update and "=" in m.status_update:
                k, v = m.status_update.split("=", 1)
                known_statuses[k.strip()] = v.strip()

        if "=" not in new_status:
            return
        new_key, new_val = new_status.split("=", 1)
        new_key = new_key.strip()
        new_val = new_val.strip()

        if new_key in known_statuses:
            old_val = known_statuses[new_key]
            # 同一key值相同 → 重复，不矛盾
            if old_val == new_val:
                return
            # 值不同 → 可能矛盾
            report.warnings.append(
                f"状态变更: {new_key} 从 '{old_val}' 变为 '{new_val}'。"
                f"请确认这不是幻觉或矛盾"
            )

    # ======== 第三道：幻觉检测 ========

    def _check_hallucination(self, coords: dict, present_people: List[str],
                             current_env: str, report: CheckReport):
        content = coords.get("content", "")
        objects = coords.get("objects", [])
        env = coords.get("environment", "")

        # 检测：提及了不在场且不在已知对象中的人物
        known_all = set(present_people or [])
        all_mems = self._store.list_all()
        for m in all_mems:
            for obj in (m.objects or []):
                known_all.add(obj)

        for obj in objects:
            if obj not in known_all and obj not in ("用户", "我", "先生", "姐姐"):
                report.warnings.append(
                    f"记忆提及未知名人物 '{obj}'，当前在场={present_people}，"
                    f"历史人物={list(known_all)[:10]}"
                )

        # 检测：content 包含明显的 LLM 幻觉特征
        hallucination_markers = [
            "作为一个AI", "根据我的训练数据", "截止我的知识",
            "请注意", "我不确定", "可能是", "也许是虚构",
        ]
        for marker in hallucination_markers:
            if marker in content:
                report.warnings.append(f"content 包含 LLM 幻觉标记: '{marker}'")
                break

        # 检测：lifecycle=dream 但内容看起来像事实陈述
        if coords.get("lifecycle_state") == DREAM:
            fact_markers = ["成功了", "完成了", "确认", "肯定", "确实是"]
            for marker in fact_markers:
                if marker in content:
                    report.warnings.append(
                        f"lifecycle=dream 但内容包含事实性表述: '{marker}'"
                    )
                    break


def create_guard(store: MemoryStore) -> ConsistencyGuard:
    return ConsistencyGuard(store)
