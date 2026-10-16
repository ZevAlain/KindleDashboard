# -*- coding: utf-8 -*-
"""世界时钟：家（北京）+ 自定义城市，IANA 时区含夏令时。"""
from datetime import timedelta

from .config import WEEKDAY_CN

# ----------------- 世界时区 / 重要日子 / 显示开关（数据模型与助手） -----------------
DEFAULT_CITY_LIST = ["纽约", "伦敦", "东京", "悉尼"]
WORLD_CITIES_TZ = {
    "北京": "Asia/Shanghai",
    "纽约": "America/New_York",
    "伦敦": "Europe/London",
    "东京": "Asia/Tokyo",
    "悉尼": "Australia/Sydney",
    "洛杉矶": "America/Los_Angeles",
    "旧金山": "America/Los_Angeles",
    "巴黎": "Europe/Paris",
    "法兰克福": "Europe/Berlin",
    "迪拜": "Asia/Dubai",
    "首尔": "Asia/Seoul",
    "新加坡": "Asia/Singapore",
    "孟买": "Asia/Kolkata",
    "莫斯科": "Europe/Moscow",
}
# 无 zoneinfo 时兜底：相对家（北京, UTC+8）的分钟偏移，仅标准时间。
WORLD_CITIES_OFF = {
    "纽约": -780, "伦敦": -480, "东京": 60, "悉尼": 120,
    "洛杉矶": -960, "旧金山": -960, "巴黎": -420, "法兰克福": -420,
    "迪拜": -240, "首尔": 60, "新加坡": 0, "孟买": -180, "莫斯科": -300,
}
try:
    from zoneinfo import ZoneInfo    # Python 3.9+
except Exception:
    ZoneInfo = None
def _world_date_cn(dt):
    return "%d月%d日 %s" % (dt.month, dt.day, WEEKDAY_CN[dt.isoweekday()])

def _city_offset(now, nm, tz, off_stored):
    """解析城市相对家（北京）的分钟偏移：IANA 时区（含夏令时）> 存储的固定偏移 >
    内置表按名查。失败返回 None（该行跳过）。永不抛异常。"""
    if tz:
        try:
            if ZoneInfo is not None:
                dt = now.astimezone(ZoneInfo(tz))
                return int((dt.utcoffset() - now.utcoffset()).total_seconds() // 60)
        except Exception:
            pass
    if isinstance(off_stored, (int, float)):
        return int(off_stored)
    tzname = WORLD_CITIES_TZ.get(nm)
    if not tzname:
        return None
    try:
        if ZoneInfo is not None:
            dt = now.astimezone(ZoneInfo(tzname))
            return int((dt.utcoffset() - now.utcoffset()).total_seconds() // 60)
    except Exception:
        pass
    return WORLD_CITIES_OFF.get(nm)

def world_rows(now, city_names):
    """家（北京）+ 各城市当前时刻。off = 相对家的分钟偏移（JS 分钟 tick 按它进退）。
    条目是 {name, tz, off}（管理页搜索添加）或纯名字字符串（旧数据）。
    永不抛异常：无法解析时区的城市直接跳过该行。"""
    rows = [{"name": "北京", "home": True, "time": now.strftime("%H:%M"),
             "date": _world_date_cn(now), "off": 0}]
    for entry in (city_names or []):
        if isinstance(entry, dict):
            nm = str(entry.get("name") or "").strip()
            tz = str(entry.get("tz") or "").strip()
            off_stored = entry.get("off")
        else:
            nm = str(entry or "").strip()
            tz, off_stored = "", None
        if not nm or nm == "北京":
            continue
        off = _city_offset(now, nm, tz, off_stored)
        if off is None:
            continue
        dt = now + timedelta(minutes=off)
        rows.append({"name": nm, "home": False,
                     "time": dt.strftime("%H:%M"), "date": _world_date_cn(dt), "off": off})
    return rows

def _norm_city_list(v):
    """世界时区城市列表归一成 [{name, tz, off}]（tz=IANA 时区、off=固定 UTC 分钟偏移，
    均可选），按名去重、上限 12。旧纯名条目用内置时区表升级；
    非 list 输入返回 None（调用方回退到现值）。"""
    if not isinstance(v, list):
        return None
    out = []
    for x in v:
        if isinstance(x, dict):
            nm = str(x.get("name") or "").strip()
            tz = str(x.get("tz") or "").strip() or WORLD_CITIES_TZ.get(nm, "")
            off, lon = x.get("off"), x.get("lon")
        else:
            nm = str(x or "").strip()
            tz, off, lon = WORLD_CITIES_TZ.get(nm, ""), None, None
        if not nm or nm == "北京":
            continue
        try:
            off = int(off)
        except (TypeError, ValueError):
            off = None
        if off is None and not tz:
            # 完全没时区：按经度估算（15°/小时，不含夏令时），好过把城市整个丢掉
            try:
                off = int(round(float(lon) / 15) * 60)
            except (TypeError, ValueError):
                off = None
        if off is None and not tz and not WORLD_CITIES_TZ.get(nm):
            continue                       # 时区无从解析 → 跳过
        if any(r["name"] == nm for r in out):
            continue
        rec = {"name": nm}
        if tz:
            rec["tz"] = tz
        if off is not None:
            rec["off"] = off
        out.append(rec)
    return out[:12]
