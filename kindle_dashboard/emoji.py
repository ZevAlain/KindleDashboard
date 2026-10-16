# -*- coding: utf-8 -*-
"""emoji 转图片（Twemoji -> 黑白单色 PNG，墨水屏优化）。"""
import html
import io
import os
import re
import time
import urllib.request

from .config import STATIC_EMOJI_DIR, TWEMOJI_URL

try:
    import emoji as _emoji  # 用于检测文本中的 emoji（官方 emoji 数据）
except Exception:
    _emoji = None

try:
    import resvg_py
    from PIL import Image  # 栅格化 Twemoji SVG -> 黑白单色 PNG（墨水屏优化）
    _SVG2IMG = True
except Exception:
    _SVG2IMG = False

# ----------------- emoji 转图片（Twemoji -> 黑白 PNG，墨水屏优化） -----------------
# Kindle 墨水屏无法直接渲染 emoji 字符。做法：后台保存时，用 `emoji` 库找出文本中
# 的每个 emoji，拉取对应 Twemoji SVG，用 resvg 栅格化 + Pillow 阈值化，转成黑白单色
# PNG 缓存到 static/emoji/ 目录，再把文本里的 emoji 替换成本地 <img>。显示页只渲染
# 本地图片：离线、不依赖 Kindle 联网，纯黑白在墨水屏上最清晰。
EMOJI_SPAN_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # 图形/表情/交通等
    "\u2600-\u27BF"           # 杂项符号、装饰符号
    "\u2B00-\u2BFF"           # 杂项符号与箭头
    "\u2300-\u23FF"           # 杂项技术（含 ⏰ 等）
    "\u2190-\u21FF"           # 箭头
    "\u25A0-\u25FF"           # 几何图形
    "][\ufe0f\u200d\U0001F000-\U0001FAFF]*"
)

def _codepoint_span(s):
    """把一段 emoji 子串转成 Twemoji 文件名用的 codepoint（去 fe0f、保留 200d）。"""
    out = []
    for c in s:
        o = ord(c)
        if o == 0xFE0F:
            continue
        if o == 0x200D:
            out.append("200d")
            continue
        out.append(format(o, "x"))
    return "-".join(out)


def _render_svg_to_png(svg_text):
    """把 SVG 渲染成 PNG 字节。兼容 resvg 0.3.x(svg_to_bytes) 与 0.1.x(RenderTree)。"""
    if hasattr(resvg_py, "svg_to_bytes"):
        # resvg >= 0.2/0.3
        return resvg_py.svg_to_bytes(svg_string=svg_text, zoom=3, background="#ffffff")
    # resvg 0.1.x（Python 3.8 环境）
    tree = resvg_py.RenderTree.from_svg_string(svg_text)
    return tree.as_png()


def _svg_to_mono_png(svg_text):
    """把 Twemoji 彩色 SVG 栅格化为黑白单色 PNG（专供墨水屏，纯黑白最清晰）。"""
    png = _render_svg_to_png(svg_text)
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    white = Image.new("RGBA", img.size, (255, 255, 255, 255))
    white.alpha_composite(img)                      # 透明背景合成到白底
    gray = white.convert("L")                       # 灰度
    gray = gray.resize((96, 96), Image.LANCZOS)     # 统一分辨率（低清源自动放大）
    bw = gray.point(lambda v: 0 if v < 150 else 255, "1")   # 阈值化 -> 纯黑白
    buf = io.BytesIO()
    bw.convert("L").save(buf, format="PNG")
    return buf.getvalue()


def _ensure_emoji_png(cp, allow_net=True):
    """确保 static/emoji/{cp}.png 存在（黑白单色，墨水屏优化）；返回 True 表示 PNG 可用。
    allow_net=False：只看本地缓存，绝不联网（display 请求路径专用，页面刷新永不卡网络）。
    下载失败落 {cp}.skip（带时间戳，7 天后允许重试），避免离线时后台线程反复空下载。"""
    png_path = os.path.join(STATIC_EMOJI_DIR, cp + ".png")
    if os.path.exists(png_path) and os.path.getsize(png_path) > 0:
        return True
    svg_path = os.path.join(STATIC_EMOJI_DIR, cp + ".svg")
    if os.path.exists(svg_path) and os.path.getsize(svg_path) > 0:
        return False   # 无栅格化库时的既有回退成果：直接用，不再重复下载
    if not allow_net:
        return False
    skip_path = os.path.join(STATIC_EMOJI_DIR, cp + ".skip")
    try:
        if time.time() - os.path.getmtime(skip_path) < 7 * 86400:
            return False    # 近期已失败过：CDN 上多半真没有，别再打请求
    except OSError:
        pass
    try:
        with urllib.request.urlopen(TWEMOJI_URL.format(cp=cp), timeout=10) as r:
            svg_bytes = r.read()
    except Exception:
        svg_bytes = b""
    if not svg_bytes:
        try:
            with open(skip_path, "w", encoding="utf-8") as f:
                f.write(str(int(time.time())))
        except Exception:
            pass
        return False
    try:
        _unlink_quiet(skip_path)
    except Exception:
        pass
    if _SVG2IMG:
        try:
            with open(png_path, "wb") as f:
                f.write(_svg_to_mono_png(svg_bytes.decode("utf-8", "replace")))
            return True
        except Exception:
            pass
    # 无栅格化库或转换失败：退回缓存原始 SVG（浏览器直接渲染，可能偏灰）
    try:
        with open(svg_path, "wb") as f:
            f.write(svg_bytes)
        return os.path.exists(png_path)
    except Exception:
        pass
    return False


def _unlink_quiet(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _img_tag(cp, allow_net=True):
    if not cp:
        return ""
    if _ensure_emoji_png(cp, allow_net=allow_net):
        return '<img class="emj" alt="" src="/static/emoji/%s.png" />' % cp
    if os.path.exists(os.path.join(STATIC_EMOJI_DIR, cp + ".svg")):
        return '<img class="emj" alt="" src="/static/emoji/%s.svg" />' % cp
    return ""


def _iter_emoji_spans(text):
    """按顺序返回 (start, end, codepoint)。优先用 emoji 库，失败则回退正则。"""
    spans = []
    if _emoji is not None:
        try:
            for m in _emoji.emoji_list(text):
                if isinstance(m, dict):
                    s, e = m["match_start"], m["match_end"]
                    sub = text[s:e]
                    try:
                        cp = _emoji.emoji_to_codepoint(sub)
                    except Exception:
                        cp = _codepoint_span(sub)
                else:
                    s, e = m[0], m[1]
                    cp = _codepoint_span(text[s:e])
                spans.append((s, e, cp))
            return spans
        except Exception:
            pass
    for m in EMOJI_SPAN_RE.finditer(text):
        spans.append((m.start(), m.end(), _codepoint_span(m.group(0))))
    return spans


def convert_emoji_to_html(text, allow_net=True):
    """把文本里的 emoji 转成内嵌本地 SVG 图片；其余文字做 HTML 转义。
    取不到图片（如离线且未抓过）时保留原始 emoji 字符，比直接丢掉强——
    后台线程联网后会自动补图并升级 text_html（见 _bg_refresh_emojis）。"""
    text = text or ""
    spans = _iter_emoji_spans(text)
    if not spans:
        return html.escape(text)
    parts = []
    last = 0
    for s, e, cp in spans:
        parts.append(html.escape(text[last:s]))
        img = _img_tag(cp, allow_net=allow_net)
        parts.append(img if img else html.escape(text[s:e]))
        last = e
    parts.append(html.escape(text[last:]))
    return "".join(parts)


def ensure_html_fields(data, allow_net=True):
    """补齐各事项 text_html；返回是否有改动。
    display 请求路径必须 allow_net=False（页面刷新绝不卡网络）。"""
    changed = False
    for it in data["items"]:
        if not it.get("text_html"):
            it["text_html"] = convert_emoji_to_html(it.get("text", ""), allow_net=allow_net)
            changed = True
    return changed
