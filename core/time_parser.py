"""时间表达式解析：处理中文相对时间 + 绝对时间识别"""
import re
from datetime import datetime, timedelta
from typing import Optional


def parse_time_expression(text: str, current_date: str = None) -> Optional[str]:
    """
    解析文本中的时间表达式，返回 YYYY-MM-DD 格式。
    支持：
    - 相对时间: "明天", "下周", "下个月", "后天", "大后天"
    - 绝对时间: "2026-01-20", "1月20号", "1月20日"
    - 星期: "下周一", "这周五"
    """
    if current_date is None:
        current_date = datetime.now().strftime("%Y-%m-%d")

    try:
        today = datetime.strptime(current_date, "%Y-%m-%d")
    except ValueError:
        today = datetime.now()

    text_lower = text.lower().strip()

    # 相对天数
    relative_days = {
        "昨天": -1, "今日": 0, "今天": 0,
        "明天": 1, "后天": 2,
        "大前天": -3, "前天": -2,
        "大后天": 3,
    }
    for word, offset in sorted(relative_days.items(), key=lambda x: -len(x[0])):
        if word in text:
            return (today + timedelta(days=offset)).strftime("%Y-%m-%d")

    # 下周/这周
    week_map = {
        "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6,
        "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6,
    }

    m = re.search(r'(下|这|上)(周|星期)([一二三四五六日1-6天])', text)
    if m:
        prefix, _, day_str = m.groups()
        target_wday = week_map.get(day_str)
        if target_wday is not None:
            delta_weeks = {"下": 1, "这": 0, "上": -1}.get(prefix, 0)
            days_until = (target_wday - today.weekday()) % 7
            if days_until == 0 and delta_weeks >= 0:
                days_until = 7 if delta_weeks > 0 else 0
            days_until += delta_weeks * 7
            return (today + timedelta(days=days_until)).strftime("%Y-%m-%d")

    # 下周（无具体星期）→ 加7天
    if "下周" in text and not re.search(r'下周[一二三四五六日]', text):
        return (today + timedelta(days=7)).strftime("%Y-%m-%d")

    # 下个月
    if "下个月" in text:
        if today.month == 12:
            return today.replace(year=today.year + 1, month=1).strftime("%Y-%m-%d")
        return today.replace(month=today.month + 1).strftime("%Y-%m-%d")

    # 绝对日期: YYYY-MM-DD 或 YYYY/MM/DD
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 中文绝对日期: X月Y号 / X月Y日
    m = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]', text)
    if m:
        year = today.year
        month = int(m.group(1))
        day = int(m.group(2))
        if month < today.month:
            year += 1
        return f"{year}-{month:02d}-{day:02d}"

    return None


def date_distance(timestamp: str, current_date: str) -> int:
    """计算两个日期的天数差"""
    try:
        t1 = datetime.strptime(timestamp, "%Y-%m-%d")
        t2 = datetime.strptime(current_date, "%Y-%m-%d")
        return abs((t1 - t2).days)
    except (ValueError, TypeError):
        return 999


def is_same_month_day(timestamp: str, current_date: str) -> bool:
    """判断是否同月同日（忽略年份，用于周年匹配）"""
    try:
        t1 = datetime.strptime(timestamp, "%Y-%m-%d")
        t2 = datetime.strptime(current_date, "%Y-%m-%d")
        return t1.month == t2.month and t1.day == t2.day
    except (ValueError, TypeError):
        return False


def is_overdue(timestamp: str, current_date: str) -> bool:
    """判断时间戳是否已过期（timestamp <= current_date）"""
    try:
        t1 = datetime.strptime(timestamp, "%Y-%m-%d")
        t2 = datetime.strptime(current_date, "%Y-%m-%d")
        return t1 <= t2
    except (ValueError, TypeError):
        return False


def date_score(timestamp: str, current_date: str, lifecycle: str = None) -> int:
    """
    日期距离计分（替代简单布尔判断）
    - 精确同日: 10
    - 同月同日（周年）: 7
    - 7天内: 5
    - 30天内: 3
    - 同一年: 2
    - 已过期且未完成的承诺 (lifecycle=pending): +3 bonus
    - 已过期但已完成的 (lifecycle=resolved): 不加分（避免旧记忆持续冒出来）
    """
    dist = date_distance(timestamp, current_date)
    if dist == 0:
        score = 10
    elif is_same_month_day(timestamp, current_date):
        score = 7
    elif dist <= 7:
        score = 5
    elif dist <= 30:
        score = 3
    elif dist <= 365:
        score = 2
    else:
        score = 1

    # 只有未完成的承诺才给过期加分
    if lifecycle == "pending" and is_overdue(timestamp, current_date):
        score += 3

    return score
