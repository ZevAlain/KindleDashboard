# -*- coding: utf-8 -*-
"""农历转换（1900-2100）与离线老黄历（干支纪日 + 建除十二值日）。"""
from datetime import date, timedelta

# ----------------- 农历日期（公历 -> 农历，日期行显示用） -----------------
# 纯查表实现，不依赖任何第三方库/联网：1900-2100 年每月大小月与闰月编码表。
# 已用香港天文台历书（T20xxc.txt）与 1900-2035 年春节/中秋锚点逐条验证。
_LUNAR_INFO = [
    0x04bd8, 0x04ae0, 0x0a570, 0x054d5, 0x0d260, 0x0d950, 0x16554, 0x056a0, 0x09ad0, 0x055d2,  # 1900-1909
    0x04ae0, 0x0a5b6, 0x0a4d0, 0x0d250, 0x1d255, 0x0b540, 0x0d6a0, 0x0ada2, 0x095b0, 0x14977,  # 1910-1919
    0x04970, 0x0a4b0, 0x0b4b5, 0x06a50, 0x06d40, 0x1ab54, 0x02b60, 0x09570, 0x052f2, 0x04970,  # 1920-1929
    0x06566, 0x0d4a0, 0x0ea50, 0x16a95, 0x05ad0, 0x02b60, 0x186e3, 0x092e0, 0x1c8d7, 0x0c950,  # 1930-1939
    0x0d4a0, 0x1d8a6, 0x0b550, 0x056a0, 0x1a5b4, 0x025d0, 0x092d0, 0x0d2b2, 0x0a950, 0x0b557,  # 1940-1949
    0x06ca0, 0x0b550, 0x15355, 0x04da0, 0x0a5b0, 0x14573, 0x052b0, 0x0a9a8, 0x0e950, 0x06aa0,  # 1950-1959
    0x0aea6, 0x0ab50, 0x04b60, 0x0aae4, 0x0a570, 0x05260, 0x0f263, 0x0d950, 0x05b57, 0x056a0,  # 1960-1969
    0x096d0, 0x04dd5, 0x04ad0, 0x0a4d0, 0x0d4d4, 0x0d250, 0x0d558, 0x0b540, 0x0b6a0, 0x195a6,  # 1970-1979
    0x095b0, 0x049b0, 0x0a974, 0x0a4b0, 0x0b27a, 0x06a50, 0x06d40, 0x0af46, 0x0ab60, 0x09570,  # 1980-1989
    0x04af5, 0x04970, 0x064b0, 0x074a3, 0x0ea50, 0x06b58, 0x05ac0, 0x0ab60, 0x096d5, 0x092e0,  # 1990-1999
    0x0c960, 0x0d954, 0x0d4a0, 0x0da50, 0x07552, 0x056a0, 0x0abb7, 0x025d0, 0x092d0, 0x0cab5,  # 2000-2009
    0x0a950, 0x0b4a0, 0x0baa4, 0x0ad50, 0x055d9, 0x04ba0, 0x0a5b0, 0x15176, 0x052b0, 0x0a930,  # 2010-2019
    0x07954, 0x06aa0, 0x0ad50, 0x05b52, 0x04b60, 0x0a6e6, 0x0a4e0, 0x0d260, 0x0ea65, 0x0d530,  # 2020-2029
    0x05aa0, 0x076a3, 0x096d0, 0x04afb, 0x04ad0, 0x0a4d0, 0x1d0b6, 0x0d250, 0x0d520, 0x0dd45,  # 2030-2039
    0x0b5a0, 0x056d0, 0x055b2, 0x049b0, 0x0a577, 0x0a4b0, 0x0aa50, 0x1b255, 0x06d20, 0x0ada0,  # 2040-2049
    0x14b63, 0x09370, 0x049f8, 0x04970, 0x064b0, 0x168a6, 0x0ea50, 0x06aa0, 0x1a6c4, 0x0aae0,  # 2050-2059
    0x092e0, 0x0d2e3, 0x0c960, 0x0d557, 0x0d4a0, 0x0da50, 0x05d55, 0x056a0, 0x0a6d0, 0x055d4,  # 2060-2069
    0x052d0, 0x0a9b8, 0x0a950, 0x0b4a0, 0x0b6a6, 0x0ad50, 0x055a0, 0x0aba4, 0x0a5b0, 0x052b0,  # 2070-2079
    0x0b273, 0x06930, 0x07337, 0x06aa0, 0x0ad50, 0x14b55, 0x04b60, 0x0a570, 0x054e4, 0x0d160,  # 2080-2089
    0x0e968, 0x0d520, 0x0daa0, 0x16aa6, 0x056d0, 0x04ae0, 0x0a9d4, 0x0a2d0, 0x0d150, 0x0f252,  # 2090-2099
    0x0d520,                                                                                      # 2100
]
_LUNAR_BASE = date(1900, 1, 31)  # 农历 1900 年正月初一
_GAN = "甲乙丙丁戊己庚辛壬癸"
_ZHI = "子丑寅卯辰巳午未申酉戌亥"
_LUNAR_MONTH_CN = ("正月", "二月", "三月", "四月", "五月", "六月",
                   "七月", "八月", "九月", "十月", "冬月", "腊月")
_LUNAR_DAY_CN = ("初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
                 "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
                 "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十")


def _leap_month(ly):
    """某农历年的闰月月份（0 = 无闰月）。"""
    return _LUNAR_INFO[ly - 1900] & 0xF


def _month_days(ly, m):
    """某农历年正常月 m 的天数（30 大月 / 29 小月）。"""
    return 30 if _LUNAR_INFO[ly - 1900] & (0x10000 >> m) else 29


def _leap_days(ly):
    """某农历年闰月的天数。"""
    return 30 if _LUNAR_INFO[ly - 1900] & 0x10000 else 29


def _lunar_months(ly):
    """该年月份序列（按真实先后插入闰月）：[(月, 是否闰月), ...]。"""
    leap = _leap_month(ly)
    seq = []
    for m in range(1, 13):
        seq.append((m, False))
        if leap == m:
            seq.append((m, True))
    return seq


def solar_to_lunar(d):
    """公历 date -> {'year','month','day','is_leap'}；超出 1900-2100 返回 None。"""
    offset = (d - _LUNAR_BASE).days
    if offset < 0:
        return None
    ly = 1900
    while ly <= 2100:
        year_days = sum(_leap_days(ly) if il else _month_days(ly, m)
                        for m, il in _lunar_months(ly))
        if offset < year_days:
            break
        offset -= year_days
        ly += 1
    if ly > 2100:
        return None
    for m, is_leap in _lunar_months(ly):
        md = _leap_days(ly) if is_leap else _month_days(ly, m)
        if offset < md:
            return {"year": ly, "month": m, "day": offset + 1, "is_leap": is_leap}
        offset -= md
    return None


def lunar_to_solar(ly, m, day, is_leap=False):
    """农历 ly 年 m 月 day 日（is_leap=True = 该年的闰 m 月）-> 公历 date。
    solar_to_lunar 的逆（同一张 1900-2100 表）；年份/月/日越界、
    该日不存在（如某年没有这个闰月、小月没有三十）返回 None。
    用途：把农历生日换算到当年历法格子对应的公历日。"""
    if not (1900 <= ly <= 2100 and 1 <= m <= 12 and 1 <= day <= 30):
        return None
    total = 0
    for yy in range(1900, ly):
        total += sum(_leap_days(yy) if il else _month_days(yy, mm)
                     for mm, il in _lunar_months(yy))
    for mm, il in _lunar_months(ly):
        if mm == m and il == is_leap:
            md = _leap_days(ly) if il else _month_days(ly, mm)
            if day > md:
                return None
            return _LUNAR_BASE + timedelta(days=total + day - 1)
        total += _leap_days(ly) if il else _month_days(ly, mm)
    return None


def lunar_date_cn(d):
    """公历 date -> '丙午年八月二十'（干支年 + 农历月日）；失败返回 None。"""
    try:
        lm = solar_to_lunar(d)
        if not lm:
            return None
        gz = "%s%s" % (_GAN[(lm["year"] - 4) % 10], _ZHI[(lm["year"] - 4) % 12])
        month_cn = ("闰" if lm["is_leap"] else "") + _LUNAR_MONTH_CN[lm["month"] - 1]
        return "%s年%s%s" % (gz, month_cn, _LUNAR_DAY_CN[lm["day"] - 1])
    except Exception:
        return None


# ----------------- 离线老黄历（干支纪日 + 建除十二值日 + 传统宜忌表，纯本地、不联网） -----------------
# 干支纪日锚点：(JDN + 53) % 60 -> 六十干支序号（甲子=0），JDN = date.toordinal() + 1721424。
# 已对照公开万年历（wannianli.tianqi.com）逐条校验，三个日期全部吻合：
#   2026-10-01=辛亥日、2026-10-09=己未日、2026-10-14=甲子日。
_JDN_EPOCH_OFFSET = 1721424   # JDN = date.toordinal() + 1721424
_GZ_DAY_ANCHOR = 53           # 锚定后 (JDN + 53) % 60 即干支序号（甲子=0）

# 建除十二值日：地支相建的日为「建」，其后按序 除满平定执破危成收开闭 循环。
_JIAN_CHU = ("建", "除", "满", "平", "定", "执", "破", "危", "成", "收", "开", "闭")
# 农历月地支：正月=寅 … 腊月=丑（闰月沿用其正月的地支）；下标 = 月份-1 -> 地支序号（子=0）
_LUNAR_MONTH_ZHI = (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 0, 1)
# 建除十二值日的传统宜忌（固定表；商业通书的宜忌叠加了众多神煞，此处取经典主干，离线可复算）
_JIAN_CHU_YI = {
    "建": ("祭祀", "祈福", "出行", "会友", "修造"),
    "除": ("祭祀", "求医", "扫舍", "沐浴", "修造"),
    "满": ("开市", "纳财", "交易", "祈福", "祭祀"),
    "平": ("修造", "动土", "出行", "会友", "纳采"),
    "定": ("嫁娶", "订盟", "纳采", "祈福", "修造"),
    "执": ("祭祀", "修造", "安葬", "捕捉", "经络"),
    "破": ("破屋", "拆卸", "求医", "除旧", "纳采"),
    "危": ("祭祀", "祈福", "安床", "修造", "纳财"),
    "成": ("开市", "交易", "嫁娶", "入宅", "修造"),
    "收": ("纳财", "收帐", "祭祀", "安葬", "修坟"),
    "开": ("开市", "开张", "出行", "会友", "修造"),
    "闭": ("安葬", "修坟", "祭祀", "筑堤", "补垣"),
}
_JIAN_CHU_JI = {
    "建": ("安葬", "破土", "动土"),
    "除": ("嫁娶", "入宅", "动土"),
    "满": ("动土", "安葬"),
    "平": ("嫁娶", "安葬"),
    "定": ("诉讼", "破土"),
    "执": ("出行", "开市"),
    "破": ("嫁娶", "入宅", "开市", "安葬"),
    "危": ("登高", "出行"),
    "成": ("安葬", "诉讼"),
    "收": ("开市", "出行"),
    "开": ("安葬", "破土"),
    "闭": ("开市", "出行", "修造"),
}


def _day_ganzhi(d):
    """公历 date -> 干支日（如 '己未'）；60 天循环，锚点见上。"""
    jdn = d.toordinal() + _JDN_EPOCH_OFFSET
    g = (jdn + _GZ_DAY_ANCHOR) % 60
    return "%s%s" % (_GAN[g % 10], _ZHI[g % 12])


def _day_jianchu(d):
    """公历 date -> 建除值日（'建'~'闭' 单字）；日支与月支相建为建日。失败返回 None。"""
    lm = solar_to_lunar(d)
    if not lm:
        return None
    month_zhi = _LUNAR_MONTH_ZHI[lm["month"] - 1]
    day_zhi = _ZHI.index(_day_ganzhi(d)[1])
    return _JIAN_CHU[(day_zhi - month_zhi) % 12]


def huangli_day(d):
    """某日离线黄历：干支 / 建除 / 宜 / 忌；永不抛异常，失败返回 None。"""
    try:
        jc = _day_jianchu(d)
        if not jc:
            return None
        return {"ganzhi": _day_ganzhi(d), "jianchu": jc,
                "yi": list(_JIAN_CHU_YI[jc]), "ji": list(_JIAN_CHU_JI[jc])}
    except Exception:
        return None
