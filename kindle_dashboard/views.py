# -*- coding: utf-8 -*-
"""全部路由与页面视图。register_views(app) 由 create_app 调用。"""
import hmac
import json
import urllib.parse
from datetime import date, datetime

from flask import make_response, redirect, render_template, request, session, jsonify

from .api_status import _api_status
from .auth import login_required
from .config import API_SOURCES, CN_TZ, WEEKDAY_CN, now_cn
from .data import (DATA_LOCK, DEFAULT_CITY_LIST, DEFAULT_DISPLAY, _get_data_locked,
                   _norm_city_list, _norm_display, _norm_special_dates,
                   _shuffle_items_for_day, get_data, item_meta, item_visible,
                   gen_client_id, load_data, normalize, save_data, valid_item_time)
from .emoji import convert_emoji_to_html
from .geocode import geocode_search
from .holidays import get_holiday_events, nearest_holiday
from .lunardate import (_LUNAR_DAY_CN, _LUNAR_MONTH_CN, huangli_day,
                        lunar_date_cn, lunar_to_solar, solar_to_lunar)
from .motto import _motto_trim, get_motto, split_motto
from .secrets import SECRET_PASSWORD
from .weather import get_weather
from .worldclock import world_rows

def _next_land(land, modes):
    # 页脚「屏幕方向」按钮点按后的下一个方向：只在白名单模式间循环（admin 可只开放几个模式）。
    if not modes:
        return 0
    if land not in modes:
        return modes[0]
    return modes[(modes.index(land) + 1) % len(modes)]

_ENG_NUM_BASE = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                 "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
                 "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]
_ENG_NUM_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty",
                 7: "seventy", 8: "eighty", 9: "ninety"}


def _num_to_english_word(n):
    """数字 → 英文词：1..20 直给（one, two, …），21..99 用 "twenty-one" 形式；
    >99 退回数字字符串（实际用不到那么多事项）。"""
    if n <= 0 or n > 99:
        return str(n)
    if n <= 20:
        return _ENG_NUM_BASE[n]
    tens, ones = divmod(n, 10)
    return _ENG_NUM_TENS[tens] + (("-" + _ENG_NUM_BASE[ones]) if ones else "")

# ----------------- 时段问候 -----------------
# 注：页眉改成「三行齐平」（方案 A）后看板已不再渲染问候语（放不进日期行：237px > 可用 205px，
# 单独占一行又会让信息列变 4 行）。函数与 payload 保留，想加回只需在模板里恢复一行。
def greeting(now):
    """时段问候：5-9 早安 / 9-11 上午好 / 11-13 中午好 / 13-17 下午好 / 17-22 晚上好 / 其余 晚安。"""
    h = now.hour
    if 5 <= h < 9:
        return "早安"
    if 9 <= h < 11:
        return "上午好"
    if 11 <= h < 13:
        return "中午好"
    if 13 <= h < 17:
        return "下午好"
    if 17 <= h < 22:
        return "晚上好"
    return "晚安"

# 每一样都会按周期把 Kindle 从休眠里叫醒（心跳还让射频根本进不了省电态），
# 这是墨水屏设备耗电的大头。后台选档后由服务端直接算好周期，模板按它出**不同的字节**：
# 周期为 0 的机制整段不下发 —— 客户端零开销（没有定时器、没有空转），也无需 JS/localStorage 配合。
POWER_PLANS = {
    # ping_ms：Wi-Fi 心跳周期；canvas_ms：canvas 保活周期；0 = 不下发
    "best":   {"ping_ms": 15000, "canvas_ms": 30000},   # 最强体验 = 改造前的原样
    "normal": {"ping_ms": 60000, "canvas_ms": 0},       # 通用（默认）：心跳降频 4 倍、去掉 canvas 空转
    "eco":    {"ping_ms": 0,     "canvas_ms": 0},       # 极致省电：射频与保活全关，屏上只剩分钟走时
}


def _power_plan(data):
    """按后台选的省电档返回 display 需要的常驻参数：
       refresh  = 整页刷新秒数（eco 档读 eco_refresh_rate，0 = 不自动刷新；其余档读 refresh_rate）
       ping_ms  = Wi-Fi 心跳周期，0 = 页面里根本没有这段 JS
       canvas_ms= canvas 保活周期，0 = 同上"""
    mode = data.get("power_mode")
    plan = POWER_PLANS.get(mode) or POWER_PLANS["normal"]
    if mode == "eco":
        try:
            refresh = int(data.get("eco_refresh_rate"))
        except (TypeError, ValueError):
            refresh = 300
        refresh = max(0, min(3600, refresh))          # 0 = 不自动刷新（页面静止）
    else:
        try:
            refresh = int(data.get("refresh_rate"))
        except (TypeError, ValueError):
            refresh = 180
        refresh = max(5, min(300, refresh))
    return {"mode": (mode if mode in POWER_PLANS else "normal"),
            "refresh": refresh,
            "ping_ms": plan["ping_ms"],
            "canvas_ms": plan["canvas_ms"]}

def _clip_countdown_name(name):
    """倒计时名字截断保护：页眉节日行独占一行、16px、竖屏 600px 宽（容量 30+ 字），
    名字留 10 个汉字上限已非常宽裕；超长截为前 9 字 + …，防极端长名撑破页眉。"""
    name = str(name or "").strip()
    return name if len(name) <= 10 else name[:9] + "…"


def _special_next_dates(data, now):
    """重要日子（生日/纪念日）的下一次发生日，返回 [(date_str, name)] 按日期升序。
    公历每年=今年该月日（已过取明年）；公历每月=本月该日（已过取下月）；
    农历=今年农历该日（已过取明年农历）。永不抛异常、永不联网。"""
    out = []
    today = now.date()
    for b in (data.get("special_dates") or []):
        nm = str(b.get("name") or "").strip()
        if not nm:
            continue
        parts = str(b.get("date") or "").split("-")
        try:
            mi, di = int(parts[-2]), int(parts[-1])
        except (ValueError, IndexError):
            continue
        try:
            if str(b.get("cal") or "solar") == "lunar":
                for ly in (today.year, today.year + 1):
                    d2 = lunar_to_solar(ly, mi, di, bool(b.get("leap")))
                    if d2 and d2 >= today:
                        out.append((d2.strftime("%Y-%m-%d"), nm))
                        break
            elif str(b.get("freq") or "yearly") == "monthly":
                ny2, nm2 = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
                for (yy, mm) in ((today.year, today.month), (ny2, nm2)):
                    try:
                        d2 = date(yy, mm, di)
                    except ValueError:
                        continue          # 本月没有 31/30 号：试下月
                    if d2 >= today:
                        out.append((d2.strftime("%Y-%m-%d"), nm))
                        break
            else:
                for yy in (today.year, today.year + 1):
                    try:
                        d2 = date(yy, mi, di)
                    except ValueError:
                        continue          # 2-29 之类：非闰年跳过取明年
                    if d2 >= today:
                        out.append((d2.strftime("%Y-%m-%d"), nm))
                        break
        except Exception:
            continue
    return sorted(out)


def _base_view(data, now, cy=None, cm=None):
    """整页共用骨架：hero（时钟/日期/农历/天气/节日/名言）+ 世界时间面板 + 万年历面板数据。
    字段全部读本地/缓存，display 路径永不联网。cy/cm 决定万年历面板渲染的月份
    （默认北京当前月；翻月整页刷新用 ?y=&m= 传入，meta 刷新携带 URL 自然保持当月）。"""
    disp = data.get("display") or {}   # 显示内容开关（admin 可配）
    lunar = lunar_date_cn(now.date()) if disp.get("show_lunar", True) else ""
    weather = (get_weather(data.get("weather") or {}, now)
               if disp.get("show_weather", True) else None)   # 没配置地区/取不到时为 None，模板不渲染天气块
    # 日期第一行：公历年 + 公历月日 + 星期（2026 9月30日 · 周三）；农历另起一行，不显示时区
    date_line = "%d %d月%d日 · %s" % (now.year, now.month, now.day, WEEKDAY_CN[now.isoweekday()])
    motto_text, motto_author = (split_motto(get_motto(now))
                                if disp.get("show_motto", True) else ("", ""))   # 页脚：第一行内容 / 第二行 ——作者
    if motto_text:
        motto_text = _motto_trim(motto_text)   # 正文超长按标点截断（缓存里的旧长句也在这里兜住）
    # 页眉倒计时：重要日子（生日/纪念日）与法定节日/节气合并，取**最近**的一个显示——
    # 自己设的日子比节气更个性化，排在它前面时优先显示（"距 妈妈生日 还有 3 天"）。
    nearest = nearest_holiday(now)
    for ds, nm in _special_next_dates(data, now)[:1]:
        if nearest is None or ds < nearest[1]:
            nearest = (nm, ds)
    holiday = None
    if nearest:
        nm, ds = _clip_countdown_name(nearest[0]), nearest[1]
        try:
            if ds == now.strftime("%Y-%m-%d"):
                holiday = "今天是 %s" % nm
            else:
                holiday = "距 %s 还有 %d 天" % (
                    nm, (datetime.strptime(ds, "%Y-%m-%d").date() - now.date()).days)
        except Exception:
            holiday = None
    # 万年历面板月份（?y=&m= 传入；越界回退当前月）
    y, m = cy, cm
    if not (1900 <= y <= 2100 and 1 <= m <= 12):
        y, m = now.year, now.month
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return {
        "refresh_rate": data["refresh_rate"],
        # 省电档（best/normal/eco）：整页刷新间隔 + 心跳/保活周期，模板按它条件渲染
        "power": _power_plan(data),
        "now_ts": int(now.timestamp()),
        "tz_min": int(CN_TZ.utcoffset(now).total_seconds() // 60),
        "clock_time": now.strftime("%H:%M"),
        "date_line": date_line,
        "lunar_line": lunar or "",
        "greeting": greeting(now),          # 看板模板当前不渲染（见 greeting() 上方注释）
        "weather": weather,
        "motto_text": motto_text,
        "motto_author": motto_author,       # 空串时不渲染第二行（本地语料无署名）
        "holiday_text": holiday,
        "holiday_html": (convert_emoji_to_html("🗓️ " + holiday, allow_net=False) if holiday else ""),
        # 世界时间面板（页内展开/收回）：家 + admin 配置城市，离线解析
        "world": world_rows(now, data.get("world_cities")),
        # 万年历面板（页内展开/收回）：整月格子 + 今日黄历（离线计算）
        "cal": {
            "title": "%d年%d月" % (y, m),
            "week": ["日", "一", "二", "三", "四", "五", "六"],
            "cells": _cal_cells(data, now, y, m),
            "prev": {"y": py, "m": pm},
            "next": {"y": ny, "m": nm},
            "today_d": now.day,
            "today_lunar": lunar_date_cn(now.date()) or "",
            "today_line": huangli_day(now.date()),
        },
    }

def _cal_cells(data, now, y, m):
    """某月日历格子：周日开局，前置空格 + 每日（农历、节气/节日、重要日子、今日高亮）。纯本地数据。"""
    import calendar as _cal
    events = {}
    for e in (get_holiday_events(now) or []):
        ds = str(e.get("date") or "")
        if ds.startswith("%04d-%02d-" % (y, m)):
            events.setdefault(ds, []).append(str(e.get("name") or ""))
    bd = {}
    for b in (data.get("special_dates") or []):
        nm = str(b.get("name") or "").strip()
        parts = str(b.get("date") or "").split("-")
        if not nm or len(parts) < 2:
            continue
        try:
            if str(b.get("cal") or "solar") == "lunar":
                # 农历日子：换算成当年对应的公历日再标格子。
                # 公历 1 月里看到的农历月属于上一个农历年（腊月会落到次年 1 月）。
                d0 = lunar_to_solar(y - 1 if m == 1 else y, int(parts[-2]), int(parts[-1]),
                                    bool(b.get("leap")))
                if d0 is not None and d0.year == y and d0.month == m:
                    bd.setdefault(str(d0.day), []).append(nm)
            else:
                em, ed = int(parts[-2]), int(parts[-1])
                if str(b.get("freq") or "yearly") == "monthly" and 1 <= ed <= 31:
                    bd.setdefault(str(ed), []).append(nm)   # 每月这一天都标记（公历）
                elif em == m and 1 <= ed <= 31:
                    bd.setdefault(str(ed), []).append(nm)
        except ValueError:
            continue
    lead = (datetime(y, m, 1).weekday() + 1) % 7   # 周日起始：日=0 … 六=6
    cells = [None] * lead
    for day in range(1, _cal.monthrange(y, m)[1] + 1):
        d = date(y, m, day)
        lun = ""
        try:
            lm = solar_to_lunar(d)
            if lm:
                lun = (("闰" if lm["is_leap"] else "") + _LUNAR_MONTH_CN[lm["month"] - 1]) \
                    if lm["day"] == 1 else _LUNAR_DAY_CN[lm["day"] - 1]
        except Exception:
            pass
        cells.append({
            "sun": day,
            "lunar": lun,
            "tag": "，".join(events.get(d.strftime("%Y-%m-%d"), [])[:1]),
            "birth": "，".join(bd.get(str(day), [])[:2]),
            "today": (d == now.date()),
        })
    while cells and len(cells) % 7:
        cells.append(None)
    return cells


def register_views(app):
    """注册全部路由（保持原 endpoint 名：display/toggle_item/admin/...）。"""


    @app.errorhandler(404)
    def _err_404(e):
        return render_template('error.html', msg="页面不存在。"), 404


    @app.errorhandler(500)
    def _err_500(e):
        return render_template('error.html', msg="服务内部错误，请过几分钟再试。"), 500


    @app.route('/login', methods=['GET', 'POST'])
    def login():
        # 登录成功后回到原本请求的页面（login_required 传了 next）；只接受站内相对路径，防开放重定向
        # （注意 WHATWG 把 /\ 当 //，老 WebKit 同样处理，必须一并挡掉）
        next_url = request.args.get('next') or ''
        safe_next = (next_url if (next_url.startswith('/')
                                  and not next_url.startswith('//')
                                  and not next_url.startswith('/\\')) else '')
        if request.method == 'POST':
            pwd = request.form.get('password')
            try:
                ok = bool(pwd) and hmac.compare_digest(str(pwd), str(SECRET_PASSWORD))
            except Exception:
                ok = False
            if ok:
                session['logged_in'] = True
                return redirect(safe_next or '/admin')
            return render_template('login.html', error="密码错误，请重试")
        return render_template('login.html')


    @app.route('/logout')
    def logout():
        session.pop('logged_in', None)
        return redirect('/login')


    @app.route('/ping')
    def ping():
        """Kindle 页面 WiFi 保活心跳：只回空 204，无数据、免登录（保活通路不依赖会话，
        会话丢了也照常用），服务端开销 ~0（原来心跳打 /display，15s 跑一整页流水线）。"""
        resp = make_response('', 204)
        resp.headers['Cache-Control'] = 'no-store'
        return resp


    @app.after_request
    def _long_cache_static_emoji(resp):
        """emoji 图片按 codepoint 命名、内容永不变化：给一年强缓存，
        免得每次整页刷新每张图都走一次 304 往返（弱 WiFi 下积少成多）。"""
        if request.path.startswith('/static/emoji/'):
            resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return resp


    # ----------------- Kindle 省电档（display 侧的三条常驻开销） -----------------
    @app.route('/')
    @app.route('/display')
    @login_required
    def display():
        data = get_data()
        now = now_cn()
        # 万年历面板月份可用 ?y=&m= 传入（翻月整页刷新携带 URL，meta 自动刷新保持当前月）。
        # 旧 ?view=world / ?view=cal 分页参数已废弃：世界时间/万年历改为页内展开/收回面板
        # （与天气对比同一机制）；老链接打开时直接落到主看板，不 404。
        cy, cm = now.year, now.month
        try:
            cy, cm = int(request.args.get("y")), int(request.args.get("m"))
        except (TypeError, ValueError):
            pass
        if not (1900 <= cy <= 2100 and 1 <= cm <= 12):
            cy, cm = now.year, now.month
        _disp = data.get("display") or {}
        visible = [it for it in data['items'] if item_visible(it, data['periods'], now)]
        # 序号（英文原顺序）：各事项在"未打乱、当前时段可见"列表中的位置，每时段从 1 重新编号
        _seq_map = ({it["id"]: _num_to_english_word(i + 1) for i, it in enumerate(visible)}
                    if _disp.get("show_seq") else {})
        # 随机顺序模式：按天打乱（同一天顺序稳定，自动刷新不打跳；次日自动换新顺序）。
        # 不勾选 = 用后台拖动调整好的默认顺序。
        if _disp.get("shuffle_items"):
            visible = _shuffle_items_for_day(visible, now)
        total = len(visible)
        done = sum(1 for it in visible if it.get("checked"))
        kindle = _base_view(data, now, cy, cm)   # 骨架：时钟/日期/农历/天气/节日/名言 + 世界时间/万年历面板数据
        kindle["progress"] = {"done": done, "total": total}   # 恒为 dict：当前时段没事项时页脚也要在（0 / 0）
        kindle["seq_pos"] = data.get("seq_pos") or "after"     # 序号位置（before/after/end）
        # 翻月链接必须带上当前 query（?key= 免登录、?land=1 横版）：写死 /display?y=&m= 会把它们一起丢掉，
        # 表现就是"在横版里点翻月、横版没了"，无 cookie 的机器上还会直接掉进登录页。
        for _nav in ("prev", "next"):
            _c = kindle["cal"][_nav]
            _c["href"] = "/display?" + urllib.parse.urlencode(
                [(k, v) for k, v in request.args.items() if k not in ("y", "m")]
                + [("y", str(_c["y"])), ("m", str(_c["m"]))])
        # 「回到今天」：翻到其他月份时出现（查看的就是当月时不显示，避免冗余按钮）
        _ty, _tm = now.year, now.month
        kindle["cal"]["today_href"] = "/display?" + urllib.parse.urlencode(
            [(k, v) for k, v in request.args.items() if k not in ("y", "m")]
            + [("y", str(_ty)), ("m", str(_tm))])
        kindle["cal"]["is_cur_month"] = (cy == _ty and cm == _tm)
        # 勾选链接同样带上当前 query（?key= 免登录、?land=、?y=&m=、?panel=）：
        # 无 cookie 的 Kindle 上 <a> 回退通路不再掉登录页，勾完也回到当前视图。
        kindle["toggle_q"] = ("?" + urllib.parse.urlencode(list(request.args.items()))
                              if request.args else "")
        kindle["items"] = [
            {
                "id": it["id"],
                "text_html": it.get("text_html") or convert_emoji_to_html(it.get("text", ""), allow_net=False),
                "checked": it["checked"],
                "meta": item_meta(it),
                "seq": (_seq_map.get(it["id"]) if _seq_map else None)
            } for it in visible
        ]
        # 横版模式：?land=0(竖版) | 1(横版90°) | 2(倒立180°) | 3(横版270°) —— 四态循环
        # 显式参数优先（含用户手动切正放的 ?land=0）；URL 没带 land（Kindle 长时间锁屏自动重启、
        # 浏览器丢了页面状态后重新打开页面）→ 按 admin 配置的默认方向渲染
        land_raw = request.args.get("land")
        if land_raw in ("0", "1", "2", "3"):
            land = land_raw
        else:
            land = str(data.get("default_land") or 0)
        _modes = data.get("land_modes") or [0, 1, 2, 3]
        kindle["land_modes"] = _modes
        kindle["land_next_landscape"] = _next_land(int(land), _modes) in (1, 3)   # 图标：下一方向是横版(90/270)→横屏图标
        # 面板展开状态（待办/天气对比/世界时钟/万年历）的主持久化通路就是 URL ?panel=
        # （localStorage/JS 只是书签重开时的兜底）。服务端直接按它出初始 display：
        # 面板开着时整页自动刷新不会先画待办再被 JS 切过去——墨水屏少一次重绘闪烁。
        panel = request.args.get("panel")
        if panel not in ("world", "cal", "cmp"):
            panel = "todo"
        resp = make_response(render_template('kindle.html', data=kindle, land=land, panel=panel))
        # 强制回源取新页面：Kindle/浏览器很可能吃着磁盘缓存，改完代码真机上还在跑旧页面，
        # 表现就是"点天气块没反应"（旧页面根本没有 onclick）——macOS 刚刷过、所以能开。
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp


    # 后台「数据源」tab 用的来源说明（纯静态文案，状态来自 _api_status）。
    @app.route('/admin/api_status')
    @login_required
    def admin_api_status():
        """PC 端后台「数据源」tab：各外部 API 最近成功/失败（只读内存状态，不联网）。"""
        import copy
        cfg = get_data().get("weather") or {}
        return jsonify({
            "now_ts": int(now_cn().timestamp()),
            "sources": API_SOURCES,
            "status": copy.deepcopy(_api_status),   # 拷贝后返回：后台线程随时在写，直接迭代可能撞 RuntimeError
            "weather_on": bool(str(cfg.get("location") or "").strip()
                               and str(cfg.get("lat") or "").strip()),
        })


    @app.route('/admin')
    @login_required
    def admin():
        data = get_data()
        return render_template('admin.html', data=data, data_json=data)


    @app.route('/admin/export')
    @login_required
    def admin_export():
        """导出全部配置为 JSON 附件（待办/时段/天气/世界时钟/重要日子/显示开关/勾选状态）。"""
        data = get_data()
        body = json.dumps(data, ensure_ascii=False, indent=2)
        fname = "kindle-dashboard-backup-%s.json" % now_cn().strftime("%Y%m%d-%H%M")
        resp = make_response(body)
        resp.headers['Content-Type'] = 'application/json; charset=utf-8'
        resp.headers['Content-Disposition'] = 'attachment; filename="%s"' % fname
        resp.headers['Cache-Control'] = 'no-store'
        return resp


    @app.route('/admin/import', methods=['POST'])
    @login_required
    def admin_import():
        """导入备份（整体覆盖当前配置）。导入数据经 normalize 规范化后原子落盘。
        备份文件里夹带的非法条目（如手改出的坏日期）同样逐条告知，绝不静默丢弃。"""
        payload = request.get_json(silent=True)
        if (not isinstance(payload, dict)
                or not isinstance(payload.get('items'), list)
                or not isinstance(payload.get('periods'), list)):
            return jsonify({"ok": False, "error": "不是有效的看板数据文件"}), 400
        rejected = []
        if payload.get('special_dates') is not None:
            payload['special_dates'] = _norm_special_dates(payload['special_dates'],
                                                           rejected)
        data = normalize(payload)
        with DATA_LOCK:
            save_data(data)
        return jsonify({"ok": True, "rejected": rejected})


    @app.route('/toggle/<item_id>', methods=['GET', 'POST'])
    @login_required
    def toggle_item(item_id):
        # 读-改-写全程持锁，防与并发整页刷新/保存互相丢写
        with DATA_LOCK:
            data = _get_data_locked()
            checked, meta, found = False, "", False
            for it in data['items']:
                if it['id'] == item_id:
                    it['checked'] = not it['checked']
                    save_data(data)
                    checked, meta, found = bool(it["checked"]), item_meta(it), True
                    break
        # 看板页内打勾（XHR POST ?js=1）：回最新副标题，前端就地改样式/文字，
        # 不整页刷新（e-ink 上整页刷新闪一下很难看）。GET 不带 js=1 仍是老行为，
        # 万一真机 JS 挂了，<a href> 直接跳过来也一样能勾上；返回时保留
        # key/land/y/m/panel 等全部状态参数（勾一下不该把横版/翻月打回原形）。
        if request.args.get('js') == '1':
            if not found:
                return jsonify({"ok": 0}), 404
            resp = jsonify({"ok": 1, "checked": checked, "meta": meta})
            resp.headers['Cache-Control'] = 'no-store'
            return resp
        back_q = urllib.parse.urlencode(
            [(k, v) for k, v in request.args.items(multi=False) if k not in ("js", "t")])
        return redirect("/display" + ("?" + back_q if back_q else ""))


    @app.route('/api/geocode')
    @login_required
    def api_geocode():
        """后台「搜索地区」：返回 open-meteo 地名候选，选中后前端自动填经纬度。需要登录。"""
        q = str(request.args.get('q') or '').strip()
        try:
            return jsonify({"ok": True, "q": q, "results": geocode_search(q, count=6)})
        except Exception as e:
            return jsonify({"ok": False, "q": q, "error": str(e), "results": []})


    @app.route('/land/<int:n>', methods=['POST'])
    @login_required
    def set_land(n):
        """页脚切换屏幕方向时记住所选方向（写为服务端默认）：
        Kindle 休眠唤醒/锁屏重启后浏览器常会丢掉 URL 参数（含 ?land=），
        重新打开时按这里记住的方向渲染——"最后使用的方向"就是期望方向，
        不会再意外回退成竖屏。方向必须在 admin 开放的白名单内。"""
        with DATA_LOCK:
            data = _get_data_locked()
            modes = data.get("land_modes") or [0, 1, 2, 3]
            if n not in modes:
                return jsonify({"ok": False, "error": "该方向未开放"}), 400
            data["default_land"] = n
            save_data(data)
        return jsonify({"ok": True})


    @app.route('/api/lunar_convert')
    @login_required
    def api_lunar_convert():
        """admin 万年历：公历⇄农历日期换算（切换历法时保持"这一天"的语义连续）。
        mode=s2l：公历 YYYY-MM-DD -> 它对应的农历数字日期 + 中文（如"农历九月初十"）；
        mode=l2s：农历 YYYY-MM-DD -> 当年对应公历日。"""
        ds = str(request.args.get('date') or '').strip()
        mode = 'l2s' if str(request.args.get('mode') or '') == 'l2s' else 's2l'
        try:
            y, m, d = (int(x) for x in ds.split("-"))
            if mode == 'l2s':
                sol = lunar_to_solar(y, m, d, False)
                if sol is None:
                    return jsonify({"ok": False, "error": "该农历日期不存在（小月无三十/无此闰月）"})
                return jsonify({"ok": True, "date": sol.strftime("%Y-%m-%d"),
                                 "cn": "公历 " + sol.strftime("%Y年%m月%d日")})
            lun = solar_to_lunar(date(y, m, d))
            if lun is None:
                return jsonify({"ok": False, "error": "日期超出支持范围（1900-2100）"})
            cn = ("闰" if lun["is_leap"] else "") + _LUNAR_MONTH_CN[lun["month"] - 1] \
                + _LUNAR_DAY_CN[lun["day"] - 1]
            return jsonify({"ok": True,
                            "date": "%04d-%02d-%02d" % (lun["year"], lun["month"], lun["day"]),
                            "cn": "农历" + cn})
        except Exception:
            return jsonify({"ok": False, "error": "日期格式错误（需要 YYYY-MM-DD）"})


    @app.route('/admin/save', methods=['POST'])
    @login_required
    def save():
        # 请求体必须是 application/json：text/plain 伪造的跨站请求直接 400 拒绝，
        # 绝不能静默当成"空 payload"继续保存——那会把整套配置清空。
        # （JSON 里缺字段没关系，下面的合并逻辑会保留现值。）
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "要求 application/json 请求体"}), 400
        try:
            refresh_rate = int(payload.get('refresh_rate', 60))
        except (TypeError, ValueError):
            refresh_rate = 60
        refresh_rate = max(5, min(300, refresh_rate))

        # 省电档：白名单外的一律回退「通用」；省电刷新间隔允许 0（=永不自动刷新），上限 1 小时
        power_mode = str(payload.get('power_mode') or '').strip()
        if power_mode not in ("best", "normal", "eco"):
            power_mode = "normal"
        try:
            eco_refresh_rate = int(payload.get('eco_refresh_rate'))
        except (TypeError, ValueError):
            eco_refresh_rate = 300
        eco_refresh_rate = max(0, min(3600, eco_refresh_rate))

        # 部分提交保护：payload 缺 items/periods/weather 时保留现值（与 world_cities/
        # special_dates 同一原则）。否则缺字段的合法 JSON 保存会把待办/时段/地区清空
        # ——前端 saveAll 总是全量提交所以平时不触发，但 API 调用/异常路径会。
        keep_periods = payload.get('periods') is None
        periods = []
        for p in (payload.get('periods') or []):
            name = str(p.get('name', '')).strip()
            start = str(p.get('start', '')).strip()
            end = str(p.get('end', '')).strip()
            if name or start or end:
                periods.append({
                    "id": str(p.get('id', '') or gen_client_id('p')),
                    "name": name,
                    "start": start,
                    "end": end
                })

        keep_items = payload.get('items') is None
        items = []
        for it in (payload.get('items') or []):
            text = str(it.get('text', '')).strip()
            if not text:
                continue
            items.append({
                "id": str(it.get('id', '') or gen_client_id('i')),
                "text": text,
                "time": valid_item_time(it.get('time')),
                "text_html": convert_emoji_to_html(text),
                "checked": bool(it.get('checked', False)),
                "freq": str(it.get('freq') or "daily"),
                "is_daily": (str(it.get('freq') or "daily") == "daily"),
                "period_ids": [str(x) for x in (it.get('period_ids') or [])]
            })

        keep_weather = payload.get('weather') is None
        w_in = payload.get('weather') or {}
        weather = {
            "location": str(w_in.get('location', '') or '').strip(),
            "lat": str(w_in.get('lat', '') or '').strip(),
            "lon": str(w_in.get('lon', '') or '').strip()
        }

        # 持锁：读原 last_reset_date 与 title，原子落盘。
        # last_reset_date 不再无条件写"今天"：否则跨零点后的保存会跳过当天零点重置，
        # 导致昨晚的"已完成"勾选滞留到今天。title 字段一并保留（避免保存一次就丢字段）。
        # 世界时区 / 重要日子 / 显示开关：payload 缺省时保留现值（避免一次保存抹掉整套设置）
        with DATA_LOCK:
            existing = load_data()
            # 部分提交：payload 没带的整块保留现值
            if keep_periods:
                periods = [dict(p) for p in (existing.get("periods") or [])]
            if keep_items:
                items = [dict(i) for i in (existing.get("items") or [])]
            if keep_weather:
                weather = dict(existing.get("weather") or {})
            # 勾选状态以服务器为准：admin 界面不提供改勾选的入口，payload 里的 checked
            # 只是 admin 页加载时的旧快照——后台页开着不关时，家人在 Kindle 上刚勾/刚取消
            # 的事项会被这次全量保存静默回滚（新加条目仍按提交值，默认未勾）。
            existing_checked = {str(it.get("id") or ""): bool(it.get("checked", False))
                                for it in (existing.get("items") or [])}
            for it in items:
                iid = str(it.get("id") or "")
                if iid in existing_checked:
                    it["checked"] = existing_checked[iid]
            # 默认屏幕方向 / 序号位置：payload 没带或非法时保留现值（避免一次保存抹掉设置）
            try:
                default_land = int(payload.get('default_land'))
            except (TypeError, ValueError):
                default_land = existing.get('default_land', 0)
            try:
                default_land = int(default_land)
            except (TypeError, ValueError):
                default_land = 0
            default_land = default_land if default_land in (0, 1, 2, 3) else 0
            # 开放方向白名单：payload 没带就保留现值；过滤到 0,1,2,3 正序；空则回退全四态（至少留一个）
            _lm_in = payload.get('land_modes')
            if isinstance(_lm_in, list):
                _lm = [m for m in (0, 1, 2, 3) if m in _lm_in] or [0, 1, 2, 3]
            else:
                _lm_ex = existing.get('land_modes') or [0, 1, 2, 3]
                _lm = [m for m in (0, 1, 2, 3) if m in _lm_ex] or [0, 1, 2, 3]
            if default_land not in _lm:
                default_land = _lm[0]   # 默认方向必须落在开放白名单内
            seq_pos = str(payload.get('seq_pos') or '').strip()
            if seq_pos not in ('before', 'after', 'end'):
                seq_pos = str(existing.get('seq_pos') or 'after').strip()
            if seq_pos not in ('before', 'after', 'end'):
                seq_pos = 'after'
            wc = _norm_city_list(payload.get('world_cities'))
            if wc is None:
                wc = _norm_city_list(existing.get('world_cities')) \
                    or _norm_city_list(list(DEFAULT_CITY_LIST))
            # 重要日子：校验被拒的条目要带回前端弹窗告知（绝不静默丢弃）；
            # payload 没带该字段（部分提交）时不校验、不产生拒绝清单。
            rejected = []
            _sd_in = payload.get('special_dates')
            sd = _norm_special_dates(_sd_in, rejected if _sd_in is not None else None)
            if sd is None:
                sd = _norm_special_dates(existing.get('special_dates'))
                if sd is None:
                    sd = _norm_special_dates(existing.get('birthdays'))   # 旧字段名兜底
                sd = sd or []
            disp = _norm_display(payload.get('display'))
            if disp is None:
                disp = dict(DEFAULT_DISPLAY)
                exd = existing.get('display')
                if isinstance(exd, dict):
                    for k in disp:
                        if k in exd:
                            disp[k] = bool(exd.get(k))
            new_data = {
                "periods": periods,
                "items": items,
                "refresh_rate": refresh_rate,
                "power_mode": power_mode,
                "eco_refresh_rate": eco_refresh_rate,
                "weather": weather,
                "world_cities": wc,
                "special_dates": sd,
                "display": disp,
                "default_land": default_land,
                "land_modes": _lm,
                "seq_pos": seq_pos,
                "title": existing.get("title", ""),
                "title_html": existing.get("title_html", ""),
                "last_reset_date": str(existing.get("last_reset_date") or "")
            }
            save_data(new_data)
        # JSON 响应（前端 fetch 读取）：被拒条目逐条带回，弹窗告知——
        # 用户可以接受"设置不了（可能设错了）"，但不能没有提示。
        return jsonify({"ok": True, "rejected": rejected})
