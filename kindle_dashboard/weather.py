# -*- coding: utf-8 -*-
"""天气（open-meteo）：抓取、缓存、展示视图（昨天/今天/明天对比）。"""
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from .config import WEEKDAY_CN, now_cn
from .geocode import geocode_cached_only
from .utils import _cache_name, _cache_read, _http_json

WEATHER_TTL = 30 * 60          # 天气半小时换一次（整页刷新间隔内够用，没必要每次请求）
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
AIR_API = "https://air-quality-api.open-meteo.com/v1/air-quality"   # 空气质量是另一个接口（同样免 key）
WIND_DIRS = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
# 蒲福风级上限（km/h）：<= 上限 -> 该级
BEAUFORT = [(1, 0), (5, 1), (11, 2), (19, 3), (28, 4), (38, 5), (49, 6),
            (61, 7), (74, 8), (88, 9), (102, 10), (117, 11), (10 ** 9, 12)]

# WMO weather code -> (图标, 中文描述)。图标为内联 SVG（纯黑线条，墨水屏友好，不依赖 emoji 联网下载）
WEATHER_MAP = {}


def _reg_weather(icon, desc, codes):
    for c in codes:
        WEATHER_MAP[c] = (icon, desc)


_reg_weather("sun", "晴", [0])
_reg_weather("sun", "大部晴朗", [1])
_reg_weather("sun-cloud", "多云", [2])
_reg_weather("cloud", "阴", [3])
_reg_weather("fog", "雾", [45, 48])
_reg_weather("rain", "毛毛雨", [51, 53, 55])
_reg_weather("rain", "冻毛毛雨", [56, 57])
_reg_weather("rain", "小雨", [61])
_reg_weather("rain", "中雨", [63])
_reg_weather("rain", "大雨", [65])
_reg_weather("rain", "冻雨", [66, 67])
_reg_weather("rain", "阵雨", [80, 81, 82])
_reg_weather("snow", "小雪", [71])
_reg_weather("snow", "中雪", [73])
_reg_weather("snow", "大雪", [75])
_reg_weather("snow", "雪粒", [77])
_reg_weather("snow", "阵雪", [85, 86])
_reg_weather("thunder", "雷阵雨", [95])
_reg_weather("thunder", "雷暴伴冰雹", [96, 99])

# 7 个手绘图标：viewBox 0 0 24 24，stroke 线条，40px 显示时笔画约 2.8px
CLOUD_PATH = '<path d="M6.8 17.6A3.9 3.9 0 0 1 10.1 11A4.5 4.5 0 0 1 16.6 11.6A3.8 3.8 0 0 1 18.9 17.6Z"/>'
WEATHER_SVG = {
    "sun": ('<circle cx="12" cy="12" r="4.6"/>'
            '<path d="M12 5.6V2.7M12 18.4V21.3M5.6 12H2.7M18.4 12H21.3'
            'M16.53 7.47L18.58 5.42M7.47 7.47L5.42 5.42'
            'M16.53 16.53L18.58 18.58M7.47 16.53L5.42 18.58"/>'),
    "sun-cloud": ('<g transform="translate(-0.6,-0.4)"><circle cx="7.4" cy="7.6" r="3"/>'
                  '<path d="M7.4 3.5V1.4M3.3 7.6H1.2M4.5 4.7L3.02 3.22M10.3 4.7L11.78 3.22"/></g>'
                  + CLOUD_PATH),
    "cloud": CLOUD_PATH,
    "fog": '<g transform="translate(0,-1.6)">' + CLOUD_PATH + '</g><path d="M8 18.6H16.6M10.2 21.4H14.4"/>',
    "rain": ('<g transform="translate(0,-1.6)">' + CLOUD_PATH + '</g>'
             '<path d="M8.6 18.4L7.6 21.4M12.6 18.4L11.6 21.4M16.6 18.4L15.6 21.4"/>'),
    "snow": ('<g transform="translate(0,-1.6)">' + CLOUD_PATH + '</g>'
             '<path d="M9.4 18.6V22.6M7.73 19.6L11.07 21.6M7.73 21.6L11.07 19.6'
             'M15 18.6V22.6M13.33 19.6L16.67 21.6M13.33 21.6L16.67 19.6"/>'),
    "thunder": ('<g transform="translate(0,-1.6)">' + CLOUD_PATH + '</g>'
                '<path d="M13.8 17.6L10.6 21.2H12.8L11.6 23"/>'),
}


# 各图标墨迹在 24×24 里的重心不一致（实测 sun-cloud 偏上 4、thunder 偏下 2.4）。
# 正数 = 内容整体下移；把重心统一挪到 13，图标才能和同一行的温度数字上下对齐。
# 数值来自 kdcheck/icon_center.py 的像素量测（三个灰度阈值结果一致）。
ICON_SHIFT = {"cloud": -0.5, "fog": -1.6, "rain": -1.6, "snow": -2.2,
              "sun": 1.2, "sun-cloud": 4.0, "thunder": -2.4}


def weather_svg(icon):
    """图标 key -> 内联 <svg>。stroke 用 currentColor 跟随文字颜色。"""
    key = icon if icon in WEATHER_SVG else "cloud"
    body = WEATHER_SVG[key]
    dy = ICON_SHIFT.get(key, 0)
    vb = "0 %g 24 24" % -dy if dy else "0 0 24 24"   # viewBox y 上移 = 内容下移
    return ('<svg class="w-icon" viewBox="%s" fill="none" stroke="currentColor" '
            'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" '
            'aria-hidden="true">%s</svg>') % (vb, body)

def wind_cn(deg, kmh):
    """风向度数 + 风速 km/h -> 「东南风 3级」；参数缺失返回空串。"""
    try:
        d = float(deg)
        speed = float(kmh)
    except (TypeError, ValueError):
        return ""
    name = WIND_DIRS[int((d + 22.5) // 45) % 8] + "风"
    level = 12
    for upper, lv in BEAUFORT:
        if speed <= upper:
            level = lv
            break
    return "%s %d级" % (name, level)


def _round_temp(v):
    try:
        return "%d" % round(float(v))
    except (TypeError, ValueError):
        return ""

def aqi_cn(aqi):
    """US AQI -> 中文等级。墨水屏不上色，只能靠文字区分。"""
    try:
        v = int(round(float(aqi)))
    except (TypeError, ValueError):
        return ""
    if v <= 50:
        return "优"
    if v <= 100:
        return "良"
    if v <= 150:
        return "轻度污染"
    if v <= 200:
        return "中度污染"
    if v <= 300:
        return "重度污染"
    return "严重污染"


def uv_cn(uv):
    """紫外线指数 -> 等级：0-2 低 / 3-5 中 / 6-7 高 / 8-10 很高 / 11+ 极高。"""
    try:
        v = float(uv)
    except (TypeError, ValueError):
        return ""
    if v <= 2:
        return "低"
    if v <= 5:
        return "中"
    if v <= 7:
        return "高"
    if v <= 10:
        return "很高"
    return "极高"


def _hourly_by_day(times, values):
    """open-meteo 的 hourly 数组 -> {日期: (当日最小, 当日最大)}。缺值跳过，永不抛异常。"""
    out = {}
    try:
        for t, v in zip(times or [], values or []):
            if v is None or not t:
                continue
            v = float(v)
            key = str(t)[:10]
            lo, hi = out.get(key, (v, v))
            out[key] = (min(lo, v), max(hi, v))
    except Exception:
        return out
    return out


def _day_cell(day):
    """对比表的一列。今天/明天同一结构，取不到的字段一律用 — 占位，两列才对得齐。"""
    # code 缺失（None）时别猜"晴"：0 对应"晴"，缺字段时猜晴会误导
    code = day.get("code")
    icon, desc = WEATHER_MAP.get(int(code), ("cloud", "")) if code is not None else ("cloud", "")
    lo, hi = _round_temp(day.get("lo")), _round_temp(day.get("hi"))
    uv_i, uv_lvl = day.get("uv"), uv_cn(day.get("uv"))
    aqi = day.get("aqi") or []
    try:
        wind = wind_cn(day.get("wdir"), day.get("wind"))
    except Exception:
        wind = ""
    def _hm(v):
        # "2026-10-09T06:12" -> "06:12"（本地时区，timezone 参数已保证）
        try:
            s = str(v)
            return s[11:16] if len(s) >= 16 and s[10] == "T" else ""
        except Exception:
            return ""
    pop, gust = day.get("pop"), day.get("gust")
    return {
        "date": str(day.get("date") or ""),
        "wd": str(day.get("wd") or ""),
        "svg": weather_svg(icon),
        "desc": desc or "—",
        "temp": ("%s°~%s°" % (lo, hi)) if (lo and hi) else "—",
        "feel": ("%s°" % _round_temp(day.get("feel"))) if _round_temp(day.get("feel")) else "—",
        "wind": wind or "—",
        "gust": ("%dkm/h" % int(round(float(gust)))) if gust is not None else "—",
        # 紫外线：等级 + 指数（光有等级看不出 6.1 和 6.9 的区别）
        "uv": ("%s %g" % (uv_lvl, float(uv_i))) if (uv_lvl and uv_i is not None) else (uv_lvl or "—"),
        # 空气：当日 AQI 区间（接口只给得到 hourly，按天聚成 min~max）
        "air": ("%d~%d" % (round(float(aqi[0])), round(float(aqi[1])))
                if len(aqi) >= 2 else "—"),
        # 湿度：接口只给"此刻"值，放今日列；明日列 —（_weather_view 里注入今日值）
        "humidity": "—",
        # 降水概率：当天最大概率
        "pop": ("%d%%" % int(round(float(pop)))) if pop is not None else "—",
        "sunrise": _hm(day.get("sunrise")) or "—",
        "sunset": _hm(day.get("sunset")) or "—",
    }


def _day_label_cls(ds, now):
    """列标题（昨天/今天/明天）+ CSS 类。按日期与今天比对、不按数组下标：
    旧 v2 缓存只有 [今天, 明天]、跨零点后 v3 缓存还没重抓时，下标都对不上，日期才对得上。"""
    try:
        mm, dd = (int(x) for x in str(ds).split("-"))
        delta = (date(now.year, mm, dd) - now.date()).days
    except Exception:
        return "", "tdy"
    if delta == -1:
        return "昨天", "tyst"
    if delta == 0:
        return "今天", "tdy"
    if delta == 1:
        return "明天", "tmm"
    return "", "tdy"


def _weather_view(d, label, now=None):
    """缓存里的原始数值 -> 模板要的展示字段。
    关键点：页眉与面板“今日”列**同口径**，都按日期匹配“今天”那一列
    （v3 缓存 = 昨天/今天/明天三列，下标 1 才是今天；旧 v2 缓存两列下标 0 是今天，
    按日期匹配两种都正确，避免页眉与面板打架）。now 可省（单测），省略时列标题为空。"""
    days = list(d.get("days") or [])
    # 按日期找“今天”列（跨零点缓存未重抓时也能找对）；找不到退回第一列
    today = days[0] if days else {}
    if now is not None:
        today_s = now.strftime("%m-%d")
        for x in days:
            if str(x.get("date") or "") == today_s:
                today = x
                break
    # 注意 code=0（晴）是最常见值，必须用 is not None 判断，不能 or 链
    # （or 会把 0 当 falsy 跳过，页眉就错拿抓取时刻的 current code，与"今天"列打架）
    code = d.get("code")
    for _src in (today.get("code"), d.get("code")):
        if _src is not None:
            try:
                code = int(_src)
                break
            except (TypeError, ValueError):
                continue
    icon, desc = WEATHER_MAP.get(code, ("cloud", ""))
    # 今日温度区间（预报高/低）
    hi, lo = _round_temp(today.get("hi")), _round_temp(today.get("lo"))
    rng = ("%s°~%s°" % (lo, hi)) if (lo and hi) else ""
    # 今日主导风向 + 最大风速：分级用原始值（先取整会让 11.4km/h 这类边界值错一级，
    # 且与对比表 _day_cell 的口径不一致）
    wdir = today.get("wdir")
    wind = wind_cn(wdir, today.get("wind"))
    loc = (label or d.get("label") or "").strip()
    # 今日体感最高（逐时最高）
    feel = _round_temp(today.get("feel"))
    aqi = today.get("aqi")
    air = ""
    if aqi:
        try:
            air = "空气 %s %s" % (aqi_cn(aqi[0]), int(round(float(aqi[0]))))
        except Exception:
            pass
    uv_lvl = uv_cn(today.get("uv"))
    # 昨天/今天/明天对比表：三列（旧 v2 缓存只有两列也照画，后台线程会强制重抓补成三列）
    cols = [_day_cell(x) for x in days[:3]]
    for c in cols:
        c["label"], c["cls"] = (_day_label_cls(c.get("date"), now)
                                if now is not None else ("", "tdy"))
    hum = today.get("humidity") or d.get("humidity")
    if cols and hum is not None:
        # 湿度只有“此刻”值，注入“今天”列（now 缺失时退回第一列，兼容旧调用）
        target = next((c for c in cols if c.get("label") == "今天"), cols[0])
        try:
            target["humidity"] = "%d%%" % int(round(float(hum)))
        except Exception:
            pass
    # 未来 8 小时降雨概率条带（面板最下方恒显示）：从"当前整点"起切 8 个整点，
    # 档位由模板按概率分（<30 浅字 / 30-59 黑框 / ≥60 反白）。
    # v3 老缓存没有逐时数据 → rain 为空，模板不渲染（后台 30 分钟内重抓出 v4 自动补上）。
    rain = []
    try:
        if now is not None:
            times = d.get("pop_times") or []
            vals = d.get("pop_vals") or []
            now_h = now.strftime("%Y-%m-%dT%H:00")
            start = None
            for i, t in enumerate(times):
                if str(t) >= now_h:
                    start = i
                    break
            if start is None and times:
                start = 0                     # 已过今天+明天全部时刻（理论边界）：从头取
            for i in range(start if start is not None else 0,
                           min((start or 0) + 8, len(times))):
                try:
                    p = int(vals[i] or 0)
                except (TypeError, ValueError):
                    p = 0
                rain.append({"h": str(times[i])[11:13], "p": p})
    except Exception:
        rain = []
    return {
        "temp": (hi + "°" if hi else ""),          # 页眉显今日最高温（更有参考价值）
        "desc": desc,
        "range": rng,
        "wind": wind,
        "feel": ("体感 " + feel + "°") if feel else "",
        "air": air,
        "uv": ("紫外线 " + uv_lvl) if uv_lvl else "",
        "desc_line": " ".join(x for x in (desc, rng) if x),
        "loc_line": " · ".join(x for x in (loc, wind) if x),
        "svg": weather_svg(icon),
        "cmp": cols if len(cols) >= 2 else [],
        "rain": rain[:8],
    }


def _fetch_air(lat, lon):
    """空气质量（open-meteo 空气专用接口，免 key）。永不抛异常：取不到返回 None。
    顺带把未来 3 天的逐时 AQI 一起取回来（它的 daily 参数不支持，400），按天聚成区间，
    用来跟今天做对比——还是同一个请求，不额外花时间。"""
    try:
        url = (AIR_API
               + "?latitude=" + urllib.parse.quote(str(lat))
               + "&longitude=" + urllib.parse.quote(str(lon))
               + "&current=us_aqi,pm2_5&hourly=us_aqi&past_days=1&forecast_days=3"
               + "&timezone=Asia%2FShanghai")
        raw = _http_json(url, timeout=5) or {}
        cur = raw.get("current") or {}
        aqi = cur.get("us_aqi")
        if aqi is None:
            return None
        hr = raw.get("hourly") or {}
        return {"aqi": aqi, "pm25": cur.get("pm2_5"),
                "aqi_time": hr.get("time"), "aqi_h": hr.get("us_aqi")}
    except Exception:
        return None

def _fetch_weather_raw(lat, lon, ts, model="best_match"):
    """真正请求 open-meteo 两个接口（仅后台线程调用），返回缓存 payload；失败抛异常。
    past_days=1 + forecast_days=3：昨天/今天/明天三天的数据同一个请求带回来，不多打接口
    （open-meteo 的 daily 参数支持回溯，昨天的高低温/天气/日出日落都能取到）；
    体感/阵风走 hourly（current 只有此刻的，后天要用逐时值取当日最高；daily 无阵风聚合字段）；
    天气 + 空气并行取（空气是另一个域名），空气挂了 _fetch_air 返回 None，天气照常存。
    model：open-meteo 气象模型（best_match 综合最优 / cma 中国气象局 / ecmwf / gfs）。"""
    if model not in WEATHER_MODELS:
        model = "best_match"
    url = (FORECAST_API
           + "?latitude=" + urllib.parse.quote(str(lat))
           + "&longitude=" + urllib.parse.quote(str(lon))
           + "&current=temperature_2m,weather_code,wind_speed_10m,wind_direction_10m,apparent_temperature,relative_humidity_2m"
           + "&hourly=apparent_temperature,wind_gusts_10m,precipitation_probability"
           + "&daily=temperature_2m_max,temperature_2m_min,uv_index_max,weather_code"
           + ",wind_speed_10m_max,wind_direction_10m_dominant,precipitation_probability_max"
           + ",sunrise,sunset"
           + "&models=" + model
           + "&forecast_days=3"
           + "&past_days=1"
           + "&timezone=Asia%2FShanghai")
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_w = ex.submit(_http_json, url, 6)
        f_a = ex.submit(_fetch_air, lat, lon)
        raw = f_w.result()
        air = f_a.result() or {}
    cur = raw.get("current") or {}
    daily = raw.get("daily") or {}
    hourly = raw.get("hourly") or {}
    feel_by_day = _hourly_by_day(hourly.get("time"), hourly.get("apparent_temperature"))
    gust_by_day = _hourly_by_day(hourly.get("time"), hourly.get("wind_gusts_10m"))
    aqi_by_day = _hourly_by_day(air.get("aqi_time"), air.get("aqi_h"))
    # 逐时降雨概率（未来 8 小时条带用）：只留 今天+明天 两天的序列，
    # 视图层按查看时刻切窗（缓存 30 分钟 TTL，窗口起点最多偏半小时，小时粒度无感）
    _nd = now_cn()
    _days2 = {_nd.strftime("%Y-%m-%d"), (_nd + timedelta(days=1)).strftime("%Y-%m-%d")}
    pop_times, pop_vals = [], []
    for _t, _v in zip(hourly.get("time") or [], hourly.get("precipitation_probability") or []):
        if str(_t)[:10] in _days2:
            pop_times.append(str(_t))
            pop_vals.append(_v)

    def _at(seq, i):
        try:
            return (seq or [])[i]
        except Exception:
            return None

    # 只留 昨天 + 今天 + 明天 三列（past_days=1 + forecast_days=3），缓存里存按天
    # 聚合好的小结（不存 96 条逐时数组）
    days = []
    for i, t in enumerate(daily.get("time") or []):
        if i >= 3:
            break
        d10 = str(t)[:10]
        try:
            wd = WEEKDAY_CN[datetime.strptime(d10, "%Y-%m-%d").isoweekday()]
        except Exception:
            wd = ""
        days.append({
            "date": d10[5:],                     # "10-09"
            "wd": wd,
            "code": _at(daily.get("weather_code"), i),
            "hi": _at(daily.get("temperature_2m_max"), i),
            "lo": _at(daily.get("temperature_2m_min"), i),
            "uv": _at(daily.get("uv_index_max"), i),
            "wind": _at(daily.get("wind_speed_10m_max"), i),
            "gust": (gust_by_day.get(d10) or (None, None))[1],
            "wdir": _at(daily.get("wind_direction_10m_dominant"), i),
            "pop": _at(daily.get("precipitation_probability_max"), i),
            "sunrise": _at(daily.get("sunrise"), i),
            "sunset": _at(daily.get("sunset"), i),
            "feel": (feel_by_day.get(d10) or (None, None))[1],
            "aqi": list(aqi_by_day.get(d10) or ()),
        })
    data = {
        "temp": cur.get("temperature_2m"),
        "code": cur.get("weather_code"),
        "wind": cur.get("wind_speed_10m"),
        "wind_dir": cur.get("wind_direction_10m"),
        "feel": cur.get("apparent_temperature"),
        # past_days=1 后 daily 下标：0=昨天 / 1=今天 / 2=明天（页眉"当前"口径见 _weather_view）
        "uv": _at(daily.get("uv_index_max"), 1),
        "humidity": cur.get("relative_humidity_2m"),
        "aqi": air.get("aqi"),
        "high": _at(daily.get("temperature_2m_max"), 1),
        "low": _at(daily.get("temperature_2m_min"), 1),
        "days": days,
        "pop_times": pop_times,       # 逐时降雨概率（今天+明天 48 点），条带按时刻切窗
        "pop_vals": pop_vals,
        "v": 4,
        "_fetched_at": ts,
    }
    if data["temp"] is None or data["code"] is None:
        raise ValueError("open-meteo 返回缺字段")
    return data


def weather_cache_key(lat, lon, model):
    """天气缓存 key：含模型维度——切换模型后 key 变化，自然强制重抓新模型数据。"""
    return _cache_name("weather", "%s,%s,%s" % (lat, lon,
                                                model if model in WEATHER_MODELS else "best_match"))


# admin 可选的天气模型（open-meteo models 参数白名单）
WEATHER_MODELS = ("best_match", "cma_grapes_global", "ecmwf_ifs04", "gfs_seamless")


def get_weather(cfg, now):
    """display 的唯一入口：只读本地缓存（天气抓取由后台线程负责；完全离线也能显示，
    永不抛异常）。没经纬度且没有地名缓存、或天气缓存还没建立 → 返回 None 不渲染天气块。"""
    try:
        cfg = cfg or {}
        location = str(cfg.get("location") or "").strip()
        if not location:
            return None
        lat = str(cfg.get("lat") or "").strip()
        lon = str(cfg.get("lon") or "").strip()
        model = str(cfg.get("model") or "best_match")
        label = location
        if not (lat and lon):
            geo = geocode_cached_only(location)
            if not geo or geo.get("lat") is None:
                return None
            lat, lon = str(geo["lat"]), str(geo["lon"])
            label = str(geo.get("label") or location)
        cached = _cache_read(weather_cache_key(lat, lon, model)) or {}
        # 不判 TTL：旧缓存比没有强（后台线程会按配置间隔刷新）；
        # 老缓存缺 days（昨天/今天/明天对比表）字段视为不兼容，后台线程会强制重抓一次
        if cached.get("temp") is None or "days" not in cached:
            return None
        return _weather_view(cached, label, now)
    except Exception:
        return None


# ----------------- 后台缓存刷新（display 请求永不联网） -----------------
# Kindle 页面请求必须毫秒级返回（e-ink 整页刷新要等页面加载完），所以所有"联网抓取"
# 都挪到这个后台线程：按 TTL 刷新三类缓存；失败后退避 10 分钟再试（不再让每次页面
