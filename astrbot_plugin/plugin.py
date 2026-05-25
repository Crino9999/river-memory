"""River Memory Plugin for AstrBot

Integration layer between AstrBot message framework and River memory system.
Handles message interception, memory ingestion, and context retrieval.

To install:
  1. Copy astrbot_plugin/ to AstrBot's plugins directory
  2. Configure character in plugin settings
  3. Set API_KEY via environment or AstrBot config
"""

import sys
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional, List

_plugin_dir = Path(__file__).parent.parent
if str(_plugin_dir) not in sys.path:
    sys.path.insert(0, str(_plugin_dir))

from core.store import MemoryStore
from core.conversation import ConversationManager
from core.logger import get_logger

log = get_logger("river.plugin")


class RiverMemoryPlugin:
    """
    River Memory Plugin for AstrBot.

    Features:
    - Automatic memory ingestion from conversations
    - Multi-user session management with physical coordinates
    - Memory-aware response generation
    - Configurable character profiles
    """

    # Plugin metadata (AstrBot convention)
    NAME = "river_memory"
    AUTHOR = "容心 / implemented by 铃兰&小九"
    VERSION = "0.2.0"
    DESC = "河 (The River) — 基于物理坐标关联索引+事件流双视图的RP记忆系统"

    def __init__(self):
        self._enabled = True
        self._character_name = "角色"
        self._character_system_prompt = ""
        self._auto_ingest = True
        self._response_mode = "replace"  # "replace" | "prefix" | "passthrough"

        self._store: Optional[MemoryStore] = None
        self._manager: Optional[ConversationManager] = None

        self._start_time = time.time()
        self._stats = {"messages_processed": 0, "memories_ingested": 0}

    # ============ AstrBot Plugin Interface ============

    def get_config_schema(self) -> Dict[str, Any]:
        """Return plugin configuration schema (AstrBot convention)"""
        return {
            "character_name": {
                "type": "string",
                "default": "蕾姆",
                "description": "角色名",
            },
            "character_system_prompt": {
                "type": "text",
                "default": "",
                "description": "角色系统指令（留空则使用插件默认）",
            },
            "auto_ingest": {
                "type": "boolean",
                "default": True,
                "description": "是否自动入库新记忆",
            },
            "response_mode": {
                "type": "select",
                "options": ["replace", "prefix", "passthrough"],
                "default": "replace",
                "description": "回复模式: replace=用记忆增强替换原回复, prefix=前缀注入记忆, passthrough=只入库不干预",
            },
        }

    def on_plugin_load(self, config: Dict[str, Any] = None):
        """Called when plugin is loaded by AstrBot"""
        if config:
            self._character_name = config.get("character_name", "蕾姆")
            self._character_system_prompt = config.get("character_system_prompt", "")
            self._auto_ingest = config.get("auto_ingest", True)
            self._response_mode = config.get("response_mode", "replace")

        self._store = MemoryStore()
        self._manager = ConversationManager(self._store)

        log.info("River plugin loaded: character=%s auto_ingest=%s mode=%s",
                 self._character_name, self._auto_ingest, self._response_mode)

    def on_plugin_unload(self):
        """Called when plugin is unloaded"""
        log.info("River plugin unloaded. Stats: messages=%d memories=%d",
                 self._stats["messages_processed"], self._stats["memories_ingested"])

    async def on_message(self, message: Dict[str, Any], context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Main message hook (AstrBot convention).

        Expects message dict with:
          - user_id: str
          - group_id: str (optional)
          - raw_message: str
          - bot_reply: str (the reply that would be sent)

        Returns:
          - Modified message dict, or None to pass through
        """
        if not self._enabled or not self._manager:
            return None

        user_id = message.get("user_id") or message.get("sender_id", "unknown")
        group_id = message.get("group_id", "")
        session_id = f"{group_id}:{user_id}" if group_id else user_id
        raw_message = message.get("raw_message") or message.get("message", "")
        bot_reply = message.get("bot_reply") or message.get("reply", "")

        if not raw_message:
            return None

        self._stats["messages_processed"] += 1

        try:
            session = self._manager.get_session(session_id, self._character_name)

            # Update physical coordinates from context
            if context.get("present_people"):
                session.present_people = context["present_people"]
            if context.get("environment"):
                session.current_env = context["environment"]

            # Auto-ingest the conversation turn
            if self._auto_ingest and bot_reply:
                ingested = self._manager.process_turn(
                    session_id, user_msg=raw_message, bot_reply=bot_reply,
                    character_name=self._character_name,
                )
                self._stats["memories_ingested"] += len(ingested)

            # Inject memory context into response based on mode
            if self._response_mode == "replace":
                enhanced = self._manager.recall_for_session(session_id, raw_message)
                message["reply"] = enhanced
                message["bot_reply"] = enhanced

            elif self._response_mode == "prefix":
                recall_result = self._manager.recall_for_session(session_id, raw_message)
                if bot_reply:
                    message["reply"] = f"{recall_result}\n---\n{bot_reply}"
                    message["bot_reply"] = message["reply"]

            elif self._response_mode == "passthrough":
                pass

        except Exception as e:
            log.error("on_message error for session=%s: %s", session_id, e)

        return message

    def on_command(self, command: str, args: List[str], context: Dict) -> Optional[str]:
        """Command handler for admin/debug commands"""
        cmd = command.lower().lstrip("/")

        if cmd == "river_stats":
            uptime = time.time() - self._start_time
            mem_count = len(self._store.list_all()) if self._store else 0
            sessions = self._manager.list_sessions() if self._manager else []
            return (
                f"[River 记忆系统]\n"
                f"状态: 运行中 (运行 {int(uptime)}s)\n"
                f"记忆总数: {mem_count}\n"
                f"活跃会话: {len(sessions)}\n"
                f"已处理消息: {self._stats['messages_processed']}\n"
                f"已入库记忆: {self._stats['memories_ingested']}"
            )

        if cmd == "river_streams" and self._manager:
            streams = {}
            all_mems = self._store.list_all()
            for m in all_mems:
                sid = m.event_stream_id or "_unaffiliated"
                streams.setdefault(sid, []).append(m)
            lines = ["[事件流一览]"]
            for sid, mems in sorted(streams.items()):
                lines.append(f"  {sid} ({len(mems)}条)")
            return "\n".join(lines) if len(lines) > 1 else "[无事件流]"

        if cmd == "river_date" and self._manager:
            if args:
                session_id = context.get("user_id", "default")
                days = int(args[0]) if args else 1
                session = self._manager.get_session(session_id)
                session.advance_date(days)
                return f"日期推进 {days} 天，当前: {session.current_date}"
            return "[用法] /river_date <天数>"

        if cmd == "river_env":
            if args and self._manager:
                session_id = context.get("user_id", "default")
                session = self._manager.get_session(session_id)
                session.current_env = " ".join(args)
                return f"环境已更新: {session.current_env}"
            return "[用法] /river_env <环境描述>"

        if cmd == "river_people":
            if args and self._manager:
                session_id = context.get("user_id", "default")
                session = self._manager.get_session(session_id)
                session.present_people = list(args)
                return f"在场人物已更新: {session.present_people}"
            return "[用法] /river_people <人名1> <人名2> ..."

        if cmd == "river_force_ingest":
            if self._manager:
                session_id = context.get("user_id", "default")
                self._manager.force_ingest_recent_history(session_id)
                return "已强制入库最近历史"

        return None


def register_plugin():
    """AstrBot plugin registration entry point"""
    return RiverMemoryPlugin()
