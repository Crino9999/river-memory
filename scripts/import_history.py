"""批量导入历史对话到 River 记忆系统

支持格式:
  JSON: [{"user": "用户消息", "bot": "角色回复", "date": "2026-01-01", "people": ["A"], "env": "厨房"}, ...]
  CSV:  user,bot,date,people,env

用法:
  python scripts/import_history.py data.json
  python scripts/import_history.py data.csv
"""

import sys
import os
import json
import csv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.store import MemoryStore
from core.ingestor import MemoryIngestor
from core.logger import get_logger

log = get_logger("import")


def import_from_json(filepath: str, store: MemoryStore = None):
    """从 JSON 文件批量导入对话记忆"""
    store = store or MemoryStore()
    ingestor = MemoryIngestor(store)

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("错误: JSON 格式应为列表 [{user, bot, date, ...}, ...]")
        return

    success, fail = 0, 0
    total = len(data)

    for i, turn in enumerate(data):
        user_msg = turn.get("user") or turn.get("user_msg", "")
        bot_reply = turn.get("bot") or turn.get("bot_reply", "")
        date = turn.get("date", "2026-01-01")
        people = turn.get("people", [])
        env = turn.get("env") or turn.get("environment", "")

        if not user_msg:
            fail += 1
            log.warning("行 %d: 跳过空消息", i + 1)
            continue

        try:
            ingested = ingestor.ingest(
                user_msg=user_msg,
                bot_reply=bot_reply,
                current_date=date,
                present_people=people,
                current_env=env,
            )
            success += 1
            if (i + 1) % 10 == 0:
                print(f"  进度: {i+1}/{total} (成功 {success}, 跳过 {fail})")
        except Exception as e:
            fail += 1
            log.error("行 %d 入库失败: %s", i + 1, e)

    print(f"\n导入完成: 总 {total} 条, 成功 {success}, 失败/跳过 {fail}")
    return store


def import_from_csv(filepath: str, store: MemoryStore = None):
    """从 CSV 文件批量导入对话记忆"""
    store = store or MemoryStore()
    ingestor = MemoryIngestor(store)

    success, fail = 0, 0
    total = 0

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        total = len(rows)

        for i, row in enumerate(rows):
            user_msg = row.get("user") or row.get("user_msg", "")
            bot_reply = row.get("bot") or row.get("bot_reply", "")
            date = row.get("date", "2026-01-01")
            people_str = row.get("people", "")
            people = [p.strip() for p in people_str.split(",") if p.strip()]
            env = row.get("env") or row.get("environment", "")

            if not user_msg:
                fail += 1
                continue

            try:
                ingested = ingestor.ingest(
                    user_msg=user_msg,
                    bot_reply=bot_reply,
                    current_date=date,
                    present_people=people,
                    current_env=env,
                )
                success += 1
                if (i + 1) % 10 == 0:
                    print(f"  进度: {i+1}/{total} (成功 {success}, 跳过 {fail})")
            except Exception as e:
                fail += 1
                log.error("行 %d 入库失败: %s", i + 1, e)

    print(f"\n导入完成: 总 {total} 条, 成功 {success}, 失败/跳过 {fail}")
    return store


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scripts/import_history.py <文件.json|文件.csv>")
        sys.exit(1)

    filepath = sys.argv[1]
    ext = Path(filepath).suffix.lower()

    if ext == ".json":
        import_from_json(filepath)
    elif ext == ".csv":
        import_from_csv(filepath)
    else:
        print(f"不支持的格式: {ext}，请使用 .json 或 .csv")
        sys.exit(1)
