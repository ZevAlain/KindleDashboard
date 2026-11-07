# -*- coding: utf-8 -*-
"""节日/节气倒计时：法定假日抓取（timor.tech）+ 节气天文算法离线计算，磁盘缓存。"""
import json
import math
import os
import urllib.request
from datetime import datetime, timedelta

from .config import CACHE_DIR
from .utils import _atomic_write_json

# ----------------- 节日/节气倒计时（联网抓取 + 磁盘缓存 + 离线节气） -----------------
HOLIDAY_API_URL = "https://timor.tech/api/holiday/year/{year}"
HOLIDAY_CACHE_TTL = 24 * 3600  # 缓存 24h 过期才联网抓一次

# ----------------- 节气：天文算法离线计算（任意年份，零联网零依赖） -----------------
# 节气 = 太阳视黄经到达 15° 整倍数的时刻（小寒=285°、立春=315°、春分=0°、冬至=270°…）。
# 用 Meeus《Astronomical Algorithms》ch.25 的低精度太阳视黄经（±0.01°，光行差+章动
# 已含）+ 牛顿迭代求根 → 换算北京时间取日期。日期精度分钟级（远优于旧静态表的 ±1 天），
# 任意年份可用——旧表只写到 2027，2028 起节气行消失、万年历其他年份也没有标签。
# 已用旧表 2026/2027 共 48 条 + 公开历书锚点逐条验证（见 git 历史）。
_TERM_NAMES = (u"小寒", u"大寒", u"立春", u"雨水", u"惊蛰", u"春分", u"清明", u"谷雨",
               u"立夏", u"小满", u"芒种", u"夏至", u"小暑", u"大暑", u"立秋", u"处暑",
               u"白露", u"秋分", u"寒露", u"霜降", u"立冬", u"小雪", u"大雪", u"冬至")
_EPOCH_JD = 2440587.5                 # 1970-01-01 00:00 UT 的儒略日
_SUN_DEG_PER_DAY = 360.0 / 365.2422   # 太阳平均每日黄经增量（迭代步长用）


def _sun_apparent_longitude(jde):
    """儒略日（UT）-> 太阳视黄经（度，0~360）。Meeus 低精度公式，±0.01°。
    忽略 ΔT（~70 秒）与更高阶摄动——对"取日期"的精度毫无影响。"""
    t = (jde - 2451545.0) / 36525.0
    l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t * t          # 几何平黄经
    m = 357.52911 + 35999.05029 * t - 0.0001537 * t * t           # 平近点角
    mr = math.radians(m)
    c = ((1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(mr)
         + (0.019993 - 0.000101 * t) * math.sin(2 * mr)
         + 0.000289 * math.sin(3 * mr))                            # 中心差
    omega = 125.04 - 1934.136 * t                                  # 月交点黄经
    return (l0 + c - 0.00569 - 0.00478 * math.sin(math.radians(omega))) % 360.0


def _term_jde(year, k):
    """某年第 k 个节气（0=小寒）的儒略日（UT）。初值按「小寒≈1月5日 + k×平均间隔」，
    太阳黄经近似匀速（每日 ~0.9856°），牛顿迭代 3~5 次即收敛到秒级。"""
    target = (285.0 + 15.0 * k) % 360.0
    jde = (_EPOCH_JD
           + (datetime(year, 1, 5) - datetime(1970, 1, 1)).total_seconds() / 86400.0
           + k * 365.2422 / 24.0)
    for _ in range(10):
        d = (target - _sun_apparent_longitude(jde) + 180.0) % 360.0 - 180.0
        if abs(d) < 1e-7:
            break
        jde += d / _SUN_DEG_PER_DAY
    return jde


_solar_terms_cache = {}   # {年: events}：纯函数结果按年缓存（整年 24 条 <1ms，
                          # display 每次请求取当年+次年；进程内存即可，无需落盘）


def _solar_term_events(year):
    """某年的 24 节气事件（离线天文计算）。永不抛异常、永不联网；单条异常跳过（防御式，
    实际不会发生）。"""
    try:
        year = int(year)
    except (TypeError, ValueError):
        return []
    if year in _solar_terms_cache:
        return _solar_terms_cache[year]
    events = []
    for k, name in enumerate(_TERM_NAMES):
        try:
            cn = (datetime(1970, 1, 1)
                  + timedelta(days=_term_jde(year, k) - _EPOCH_JD)
                  + timedelta(hours=8))          # UT -> 北京时间，取日期
            events.append({"name": name, "date": cn.strftime("%Y-%m-%d"), "type": "solar"})
        except Exception:
            continue
    events.sort(key=lambda e: e["date"])
    _solar_terms_cache[year] = events
    return events


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
    # timeout 10s：timor.tech 的 SSL 握手经常慢，5 秒不够会频繁假性失败
    with urllib.request.urlopen(req, timeout=10) as r:
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
        nxt = nearest_holiday(now)
        if not nxt:
            return None
        name, ds = nxt
        if ds == now.strftime("%Y-%m-%d"):
            return "今天是 %s" % name
        delta = (datetime.strptime(ds, "%Y-%m-%d").date() - now.date()).days  # 按整天差
        return "距 %s 还有 %d 天" % (name, delta)
    except Exception:
        return None


def nearest_holiday(now):
    """最近的法定节日/节气，返回 (name, date_str) 或 None（供与重要日子合并比较）。"""
    try:
        events = get_holiday_events(now) or []
        today = now.strftime("%Y-%m-%d")
        for e in events:
            if str(e.get("date") or "") >= today:
                return str(e.get("name") or ""), str(e["date"])
    except Exception:
        pass
    return None


# ----------------- 页脚鼓励语（一言 API + 本地语料兜底，避免永远同一句） -----------------
# 与节日倒计时同一套策略：联网取一句 → 磁盘缓存 TTL 内不重复请求 → 失败退过期缓存
