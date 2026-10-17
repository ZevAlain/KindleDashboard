# -*- coding: utf-8 -*-
"""节日/节气倒计时：法定假日抓取（timor.tech）+ 静态节气表，磁盘缓存。"""
import json
import os
import urllib.request
from datetime import datetime

from .config import CACHE_DIR
from .utils import _atomic_write_json

# ----------------- 节日/节气倒计时（联网抓取 + 磁盘缓存 + 静态兜底） -----------------
HOLIDAY_API_URL = "https://timor.tech/api/holiday/year/{year}"
HOLIDAY_CACHE_TTL = 24 * 3600  # 缓存 24h 过期才联网抓一次

# 节气静态表（近似公历日期，±1 天）：与在线数据合并提供节气；离线时兜底。仅覆盖当前/次年。
SOLAR_TERMS = {
    2026: [(1, 5, "小寒"), (1, 20, "大寒"), (2, 4, "立春"), (2, 18, "雨水"), (3, 5, "惊蛰"), (3, 20, "春分"),
           (4, 5, "清明"), (4, 20, "谷雨"), (5, 5, "立夏"), (5, 21, "小满"), (6, 5, "芒种"), (6, 21, "夏至"),
           (7, 7, "小暑"), (7, 23, "大暑"), (8, 7, "立秋"), (8, 23, "处暑"), (9, 7, "白露"), (9, 23, "秋分"),
           (10, 8, "寒露"), (10, 23, "霜降"), (11, 7, "立冬"), (11, 22, "小雪"), (12, 7, "大雪"), (12, 22, "冬至")],
    2027: [(1, 5, "小寒"), (1, 20, "大寒"), (2, 4, "立春"), (2, 18, "雨水"), (3, 5, "惊蛰"), (3, 20, "春分"),
           (4, 4, "清明"), (4, 20, "谷雨"), (5, 5, "立夏"), (5, 20, "小满"), (6, 5, "芒种"), (6, 21, "夏至"),
           (7, 7, "小暑"), (7, 22, "大暑"), (8, 7, "立秋"), (8, 23, "处暑"), (9, 7, "白露"), (9, 23, "秋分"),
           (10, 8, "寒露"), (10, 24, "霜降"), (11, 7, "立冬"), (11, 22, "小雪"), (12, 7, "大雪"), (12, 21, "冬至")],
}


def _solar_term_events(year):
    """某年的 24 节气事件（静态表）。"""
    return [{"name": n, "date": "%04d-%02d-%02d" % (year, m, d), "type": "solar"}
            for m, d, n in SOLAR_TERMS.get(year, [])]


def _holiday_cache_path(year):
    return os.path.join(CACHE_DIR, "holidays_%d.json" % year)


def _load_holiday_cache(year):
    """读某年节假日缓存。返回 (events 或 None, _fetched_at)。
    注意：空列表也算有效缓存（次年安排未发布时接口正常返回空——当作"已确认无数据"，
    TTL 内不再重抓；返回 None 只表示"没有可用缓存文件/文件损坏，需要抓取"）。"""
    try:
        with open(_holiday_cache_path(year), 'r', encoding='utf-8') as f:
            data = json.load(f)
        ev = data.get("events")
        return (ev if isinstance(ev, list) else None), int(data.get("_fetched_at") or 0)
    except Exception:
        return None, 0


def fetch_holidays(year, fetched_at):
    """从 API 抓取某年节假日安排并写缓存 cache/holidays_{year}.json；失败抛异常（由调用方兜底）。"""
    req = urllib.request.Request(HOLIDAY_API_URL.format(year=year),
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = json.loads(r.read().decode("utf-8", "replace"))
    if raw.get("code") != 0:
        raise ValueError("holiday api code != 0")
    first = {}
    for key, v in (raw.get("holiday") or {}).items():
        if not v.get("holiday"):  # 跳过补班日（调休工作日不是节日）
            continue
        name = str(v.get("name") or "").strip()
        date = str(v.get("date") or ("%d-%s" % (year, key)))
        if name and (name not in first or date < first[name]):
            first[name] = date  # 同名节日（如元旦 3 天）取最早日期
    events = [{"name": n, "date": d, "type": "holiday"}
              for n, d in sorted(first.items(), key=lambda x: x[1])]
    os.makedirs(CACHE_DIR, exist_ok=True)
    _atomic_write_json(_holiday_cache_path(year), {"_fetched_at": fetched_at, "events": events})
    return events


def get_holiday_events(now):
    """display 的唯一入口：只读本地缓存 + 静态节气，永不抛错、绝不联网
    （联网抓取由后台线程负责；离线 = 显示旧缓存 + 节气，页面不卡网络）。
    返回按日期升序的事件列表或 None。"""
    try:
        events = []
        for y in (now.year, now.year + 1):
            cached, _fetched_at = _load_holiday_cache(y)
            if cached:
                events.extend(cached)
            events.extend(_solar_term_events(y))
        if not events:
            return None
        return sorted(events, key=lambda e: e["date"])
    except Exception:
        return None


def holiday_line(now):
    """最近一个节日/节气（含跨到次年）的单行文案：今天命中=“今天是 X”，否则“距 X 还有 n 天”；无事件返回 None。"""
    try:
        events = get_holiday_events(now)
        if not events:
            return None
        today = now.strftime("%Y-%m-%d")
        future = [e for e in events if e["date"] >= today]
        if not future:
            return None
        nxt = future[0]
        if nxt["date"] == today:
            return "今天是 %s" % nxt["name"]
        delta = (datetime.strptime(nxt["date"], "%Y-%m-%d").date() - now.date()).days  # 按整天差
        return "距 %s 还有 %d 天" % (nxt["name"], delta)
    except Exception:
        return None


# ----------------- 页脚鼓励语（一言 API + 本地语料兜底，避免永远同一句） -----------------
# 与节日倒计时同一套策略：联网取一句 → 磁盘缓存 TTL 内不重复请求 → 失败退过期缓存
