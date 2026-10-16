# -*- coding: utf-8 -*-
"""data.json 数据层：默认值、原子读写、字段规范化、每日零点重置、时段/周期过滤。"""
import json
import os
import random
import re
import shutil
import threading
from datetime import date, datetime

from .config import CN_TZ, DATA_FILE, WEEKDAY_CN, now_cn
from .emoji import ensure_html_fields
from .lunardate import lunar_to_solar
from .worldclock import DEFAULT_CITY_LIST, _norm_city_list

DEFAULT_DATA = {
    "periods": [
        {"id": "p1", "name": "早晨", "start": "07:00", "end": "10:00"},
        {"id": "p2", "name": "晚上", "start": "19:00", "end": "22:00"}
    ],
    "items": [
        {"id": "i1", "text": "喝一杯温水", "checked": False, "is_daily": True, "freq": "daily", "period_ids": []},
        {"id": "i2", "text": "拉伸十分钟", "checked": False, "is_daily": True, "freq": "daily", "period_ids": []},
        {"id": "i3", "text": "阅读 30 页", "checked": False, "is_daily": True, "freq": "daily", "period_ids": ["p1"]},
        {"id": "i4", "text": "复盘今天", "checked": False, "is_daily": True, "freq": "weekly:1", "period_ids": ["p1"]}
    ],
    "refresh_rate": 180,
    # Kindle 省电模式（后台可切换，display 按它出不同的字节）：
    #   best   = 最强体验：Wi-Fi 心跳 15s + canvas 保活 30s + 分钟走时 + 按 refresh_rate 整页刷新（原样）
    #   normal = 通用（默认）：心跳降频 60s、关 canvas 保活、保留分钟走时、refresh_rate 不变 —— 肉眼无差别，射频省 75%
    #   eco    = 极致省电：完全不发心跳/不跑保活，只留分钟走时，整页刷新用 eco_refresh_rate（0=不自动刷新）
    "power_mode": "normal",
    "eco_refresh_rate": 300,                            # 仅 eco 档生效：整页刷新间隔（0=永不自动刷新）
    "weather": {"location": "", "lat": "", "lon": ""},   # 留空地区 = 不显示天气块
    "world_cities": ["纽约", "伦敦", "东京", "悉尼"],      # 世界时区页城市（家=北京恒显示）
    "special_dates": [],                                    # 重要日子 [{"name","date":"YYYY-MM-DD","cal":"solar|lunar","leap"?}]，万年历格子标记
    "display": {"show_weather": True, "show_motto": True, "show_lunar": True},   # 显示内容开关
    "last_reset_date": ""
}
def load_data():
    """依次尝试 data.json → data.json.bak；主文件损坏时自动落到备份，
    避免一次损坏就静默回退默认值（默认值一旦写回，用户数据就真没了）。"""
    for path in (DATA_FILE, DATA_FILE + ".bak"):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return json.loads(json.dumps(DEFAULT_DATA))


def save_data(data):
    """先写临时文件 + fsync，再原子替换：写一半崩溃不会损坏 data.json。
    备份策略：只把「还能正常解析」的旧版本写入 .bak——绝不用损坏文件覆盖好备份；
    当前 data.json 已损坏时先原样留存为 .corrupt 供手工抢救。调用方须持有 DATA_LOCK。"""
    tmp_path = DATA_FILE + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(DATA_FILE):
        old_ok = False
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                old_ok = isinstance(json.load(f), dict)
        except Exception:
            old_ok = False
        try:
            if old_ok:
                shutil.copy2(DATA_FILE, DATA_FILE + ".bak")
            else:
                shutil.copy2(DATA_FILE, DATA_FILE + ".corrupt")
        except Exception:
            pass
    os.replace(tmp_path, DATA_FILE)


def normalize(data):
    """补齐旧数据缺失的新字段（periods / period_ids / last_reset_date 等）。"""
    data.setdefault("refresh_rate", 180)
    # 省电模式（旧 data.json 没有这两个键时补上；值非法时回退通用档，绝不让 display 崩）
    if data.get("power_mode") not in ("best", "normal", "eco"):
        data["power_mode"] = "normal"
    try:
        data["eco_refresh_rate"] = int(data.get("eco_refresh_rate"))
    except (TypeError, ValueError):
        data["eco_refresh_rate"] = 300
    data["eco_refresh_rate"] = max(0, min(3600, data["eco_refresh_rate"]))
    # 默认屏幕方向（URL 无 ?land= 参数时生效）：0 竖版 / 1 横版90° / 2 倒立180° / 3 横版270°。
    # Kindle 长时间锁屏后可能自动重启，浏览器丢页面状态（URL 参数没了），重新打开页面
    # 时按这里配置的默认方向渲染——"倒着摆"的机器醒来仍然是倒放。
    try:
        data["default_land"] = int(data.get("default_land", 0))
    except (TypeError, ValueError):
        data["default_land"] = 0
    if data["default_land"] not in (0, 1, 2, 3):
        data["default_land"] = 0
    # 屏幕方向白名单：页脚「屏幕方向」按钮只在这些模式间循环（如只开 0/2 = 正放⇄倒放）。
    # 按 0,1,2,3 正序存储；空/非法回退全四态。默认方向必须落在白名单内。
    _lm = data.get("land_modes")
    _lm = [m for m in (0, 1, 2, 3) if isinstance(_lm, list) and m in _lm] or [0, 1, 2, 3]
    data["land_modes"] = _lm or [0]
    if data["default_land"] not in data["land_modes"]:
        data["default_land"] = data["land_modes"][0]
    # 序号位置：after=紧接事项后面（默认，原行为）/ before=事项前面 / end=每行末尾固定位置
    if data.get("seq_pos") not in ("before", "after", "end"):
        data["seq_pos"] = "after"
    data.setdefault("periods", [])
    data.setdefault("items", [])
    data.setdefault("last_reset_date", "")
    w = data.get("weather")
    if not isinstance(w, dict):
        w = data["weather"] = {}
    w.setdefault("location", "")
    w.setdefault("lat", "")
    w.setdefault("lon", "")
    for i, p in enumerate(data["periods"]):
        p.setdefault("id", "p%d" % i)
        p.setdefault("name", "")
        p.setdefault("start", "")
        p.setdefault("end", "")
    for i, it in enumerate(data["items"]):
        it.setdefault("id", "i%d" % i)
        it.setdefault("text", "")
        it.setdefault("checked", False)
        it.setdefault("is_daily", False)
        it.setdefault("period_ids", [])
        # 可选时间标签（"HH:MM"，空 = 不显示）：旧数据没有该键；非法格式清空
        it["time"] = valid_item_time(it.get("time"))
        # 迁移：旧版用 is_daily 布尔；新版用 freq（daily / weekly:N / monthly:N）。
        # 旧数据所有事项都每天显示，故统一迁移为 daily，不改变原有显示行为。
        if not it.get("freq"):
            it["freq"] = "daily"
    # 补齐新字段：世界时区 / 重要日子 / 显示开关（旧 data.json 没有这些键）
    wc = data.get("world_cities")
    if not isinstance(wc, list):
        data["world_cities"] = _norm_city_list(list(DEFAULT_CITY_LIST))
    else:
        data["world_cities"] = _norm_city_list(wc)   # 旧纯名条目升级为 {name, tz, off}
    sd = data.get("special_dates")
    if not isinstance(sd, list):
        sd = data.get("birthdays")   # 旧字段名迁移：生日 -> 重要日子
        sd = sd if isinstance(sd, list) else []
    data["special_dates"] = _norm_special_dates(sd) or []
    disp = data.get("display")
    if not isinstance(disp, dict):
        data["display"] = dict(DEFAULT_DISPLAY)
    else:
        for k in DEFAULT_DISPLAY:
            disp.setdefault(k, DEFAULT_DISPLAY[k])
    return data
DEFAULT_DISPLAY = {"show_weather": True, "show_motto": True, "show_lunar": True, "shuffle_items": False, "show_seq": False}

# 城市 -> IANA 时区名。zoneinfo 在 Python 3.9+ 可用（正确处理夏令时）；
# 老版本退回 WORLD_CITIES_OFF（标准时间，不含夏令时，夏令时期间最多差 1 小时）。
def _norm_special_dates(v):
    """重要日子（万年历标记）：只保留 {name, date, cal} 条目，上限 20。
    date = YYYY-MM-DD（旧 MM-DD 自动补当前年份，升级为完整日期）；
    cal = solar(公历) / lunar(农历，默认公历)；leap = 农历闰月（可选布尔）；
    freq = monthly（公历专用：每月这一天都标记，默认 yearly 每年这个月日）。
    非 list 输入返回 None（调用方回退到现值）。"""
    if not isinstance(v, list):
        return None
    now_y = int(datetime.now(CN_TZ).year)
    out = []
    for b in v:
        if not isinstance(b, dict):
            continue
        name = str(b.get('name') or '').strip()
        ds = str(b.get('date') or '').strip()
        parts = ds.split('-')
        if not name or len(parts) not in (2, 3):
            continue
        try:
            mi, di = int(parts[-2]), int(parts[-1])
            yi = int(parts[0]) if len(parts) == 3 else now_y
            ds = '%04d-%02d-%02d' % (yi, mi, di)
        except ValueError:
            continue
        cal = 'lunar' if str(b.get('cal') or 'solar').strip().lower() == 'lunar' else 'solar'
        if cal == 'solar':
            try:
                date(yi, mi, di)
            except ValueError:
                continue                       # 不存在的公历日（如 2026-02-30）
        else:
            leap = bool(b.get('leap'))
            if di > 30 or (1900 <= yi <= 2100 and lunar_to_solar(yi, mi, di, leap) is None):
                continue                       # 不存在的农历日（小月无三十 / 该年无此闰月）
        freq = ('monthly' if cal == 'solar'
                and str(b.get('freq') or 'yearly').strip().lower() == 'monthly' else 'yearly')
        rec = {"name": name, "date": ds, "cal": cal}
        if cal == 'lunar' and bool(b.get('leap')):
            rec["leap"] = True
        if freq == 'monthly':
            rec["freq"] = 'monthly'            # 公历专用：每月这一天都标记（农历天然按年）
        if not any(r["name"] == name and r["date"] == ds for r in out):
            out.append(rec)
    return out[:20]
def _norm_display(v):
    """非 dict 输入返回 None（调用方回退到现值）；否则返回全部显示开关（布尔化）。"""
    if not isinstance(v, dict):
        return None
    out = dict(DEFAULT_DISPLAY)
    for k in out:
        out[k] = bool(v.get(k, out[k]))
    return out
def _shuffle_items_for_day(items, now):
    """随机顺序模式（display.shuffle_items）：用"当天日期 + 事项 id 序列"做种子确定性打乱。
    同一天顺序稳定（180 秒自动整页刷新不打跳）；第二天自动换一种顺序；无需落盘保存顺序。
    少于 2 条 = 没有可打乱的，原样返回。"""
    if len(items) < 2:
        return items
    seed = "%s|%s" % (now.strftime("%Y-%m-%d"), ",".join(str(it.get("id", "")) for it in items))
    rng = random.Random(seed)
    out = list(items)
    rng.shuffle(out)
    return out

DATA_LOCK = threading.Lock()   # data.json 读-改-写锁：dev server 多线程，防并发勾选/保存互相丢写


def _get_data_locked():
    """加载数据，补齐字段/图片化字段（绝不联网取图），并按中国时间做每日零点勾选重置。
    只在有变化时写盘。必须持有 DATA_LOCK 调用。"""
    data = normalize(load_data())
    changed = ensure_html_fields(data, allow_net=False)
    now = now_cn()
    today = now.strftime("%Y-%m-%d")
    if data.get("last_reset_date") != today:
        for it in data["items"]:
            it["checked"] = False
        data["last_reset_date"] = today
        changed = True
    if changed:
        save_data(data)
    return data


def get_data():
    """公共入口：带锁读数据。路由里做读-改-写时用 _get_data_locked 并自行持锁。"""
    with DATA_LOCK:
        return _get_data_locked()
def _to_minutes(hhmm):
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def period_active(p, now):
    """判断某时段此刻是否生效。支持跨午夜（如 22:00-02:00）。"""
    a = _to_minutes(p.get("start", ""))
    b = _to_minutes(p.get("end", ""))
    if a is None or b is None:
        return True
    t = now.hour * 60 + now.minute
    if a <= b:
        return a <= t < b
    return t >= a or t < b

def freq_active(freq, now):
    """判断事项此刻是否按周期显示。daily=每天；weekly:N=周N(1=一..7=日)；monthly:N=N号(1-28)。"""
    f = (freq or "daily").strip() or "daily"
    if f == "daily":
        return True
    if f.startswith("weekly:"):
        try:
            return now.isoweekday() == int(f.split(":", 1)[1])
        except Exception:
            return True
    if f.startswith("monthly:"):
        try:
            return now.day == int(f.split(":", 1)[1])
        except Exception:
            return True
    return True


def freq_label(freq):
    """周期在显示页的简短标签：每天 / 周二 / 5号。"""
    f = (freq or "daily").strip() or "daily"
    if f == "daily":
        return "每天"
    if f.startswith("weekly:"):
        try:
            return WEEKDAY_CN[int(f.split(":", 1)[1])]
        except Exception:
            return "每周"
    if f.startswith("monthly:"):
        try:
            return "%d号" % int(f.split(":", 1)[1])
        except Exception:
            return "每月"
    return ""


def valid_item_time(v):
    """校验事项的可选时间标签：合法返回 "HH:MM"，非法/空返回 ""（空 = 不显示）。"""
    v = str(v or "").strip()
    return v if re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v) else ""


def item_meta(item):
    """事项下方副标题：可选时间 + 周期 + 完成状态，如 "07:30 · 每天 · 已完成"。
    时间是可选标签：没填就完全不出现（不留占位），其余行为不变。"""
    parts = []
    t = valid_item_time(item.get("time"))
    if t:
        parts.append(t)
    label = freq_label(item.get("freq"))
    if label:
        parts.append(label)
    if item.get("checked"):
        parts.append("已完成")
    return " · ".join(parts)


def item_visible(item, periods, now):
    """未分配时段=全天显示；已分配时段=至少一个时段生效才显示。再叠加周期(日期)过滤。"""
    if not freq_active(item.get("freq"), now):
        return False
    pids = item.get("period_ids") or []
    if not pids:
        return True
    pmap = {p.get("id"): p for p in periods}
    for pid in pids:
        p = pmap.get(pid)
        if p and period_active(p, now):
            return True
    return False
def gen_client_id(prefix):
    """服务端为缺失 id 的条目补齐占位 id（正常情况下 id 由前端生成）。"""
    return "%s_%d" % (prefix, int(datetime.now(CN_TZ).timestamp() * 1000))
