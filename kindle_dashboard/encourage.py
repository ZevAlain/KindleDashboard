# -*- coding: utf-8 -*-
"""鼓励模式（打勾彩蛋）：多人物图管理 + 配置规范化 + 话术渲染。

设计要点：
- 人物图只存数据卷（DATA_DIR/static/encourage/），不进仓库——版权形象
  （如汪汪队）由用户自行上传，仓库与公开镜像保持干净。
- 多人物 + 双随机：话术随机（phrases）与人物随机（rand_char 开=imgs 里
  随机一张 / 关=固定用 img 指的那张）互不干扰；imgs 由上传/删除接口管理，
  /admin/save 只管开关/名字/话术/随机选择，永远不动 imgs 列表本身。
- display 渲染路径零联网：图是本地的，话术/候选图列表由服务端渲染下发，
  JS 只做随机挑选与显示。
- 图片按墨水屏优化：透明底先合成白底（否则透明区按 RGB(0,0,0) 会变成黑块，
  与 emoji 管线同一坑）→ 转灰度 → 高度 ≤240px；缺 Pillow 时退回存原图（限 500KB）。
"""
import io
import os
import re
import time

from .config import DATA_DIR

ENC_DIR = os.path.join(DATA_DIR, "static", "encourage")
ENC_IMG_MAX_UPLOAD = 5 * 1024 * 1024     # 上传原图上限 5MB
ENC_IMG_HEIGHT = 240                     # 上屏高度上限（像素）：弹窗尺寸 & Kindle 内存友好
ENC_MAX_IMGS = 8                         # 人物图上限（候选全部下发到页面，8 张封顶）
DEFAULT_PHRASES = [u"{n}，你太棒了！", u"干得漂亮，{n}！", u"{n} 真是太厉害了！"]
# 只认本模块生成的命名（char_<时间戳>.png 及一期遗留的 char.png），杜绝路径注入
_IMG_NAME_RE = re.compile(r"^char[\w-]*\.png$", re.IGNORECASE)


def norm_encourage(v):
    """鼓励模式配置规范化：{on, name, phrases[], rand_char, img, imgs[]}。
    非 dict 输入返回 None（调用方回退现值，与 _norm_city_list 同一约定）。
    imgs/img 文件名一律白名单校验；img 必须落在 imgs 内，不在（或没选）回退第一张。"""
    if not isinstance(v, dict):
        return None
    on = bool(v.get("on"))
    name = str(v.get("name") or "").strip()[:20]
    phrases = []
    for p in (v.get("phrases") or []):
        p = str(p or "").strip()[:60]
        if p and p not in phrases:
            phrases.append(p)
    if not phrases:
        phrases = list(DEFAULT_PHRASES)
    rand_char = bool(v.get("rand_char"))
    imgs = []
    for f in (v.get("imgs") or []):
        f = os.path.basename(str(f or "").strip())
        if _IMG_NAME_RE.match(f) and f not in imgs:
            imgs.append(f)
    img = os.path.basename(str(v.get("img") or "").strip())
    if not _IMG_NAME_RE.match(img):
        img = ""
    if img and img not in imgs:
        imgs.append(img)            # 手改/异常数据兜底：img 并进候选列表，别让它凭空丢
    imgs = imgs[:ENC_MAX_IMGS]
    if not img and imgs:
        img = imgs[0]               # 没选固定人物 = 用第一张
    if imgs and img not in imgs:
        img = imgs[0]
    return {"on": on, "name": name, "phrases": phrases[:20],
            "rand_char": rand_char, "img": img, "imgs": imgs}


def render_phrases(enc):
    """话术 -> 最终文案列表（服务端渲染好，display 的 JS 只管随机挑一条）：
    {n} 替换成名字；没配名字时把 {n} 连同其后紧跟的标点一起清掉
    （"{n}，你太棒了！" -> "你太棒了！"，不留悬空逗号）；
    emoji 字符直接剔除——弹窗文字走 textContent，没有 emoji 转图管线，
    Kindle 渲染不了 emoji 字符（constraints 文档既有结论），留着只会出豆腐块。"""
    from .emoji import EMOJI_SPAN_RE
    name = str(enc.get("name") or "").strip()
    out = []
    for p in (enc.get("phrases") or []):
        s = str(p or "").strip()
        if not s:
            continue
        if name:
            s = s.replace("{n}", name)
        else:
            s = re.sub(r"\{n\}\s*[\s，。！？、；：,!?;:]*", "", s)
        s = EMOJI_SPAN_RE.sub("", s)
        if s:
            out.append(s)
    return out or [u"你太棒了！"]


# ----------------- 人物图文件管理 -----------------

def file_path(fname):
    return os.path.join(ENC_DIR, str(fname or ""))


def file_exists(fname):
    return bool(fname) and os.path.isfile(file_path(fname))


def file_url(fname):
    """某张图的 URL（带 ?v=mtime 版本号：换图/重传后地址变化，Kindle 自然取新图；
    文件不存在返回空串）。"""
    if not fname:
        return ""
    try:
        v = int(os.path.getmtime(file_path(fname)))
    except OSError:
        return ""
    return "/static/encourage/%s?v=%d" % (fname, v)


def img_urls(enc):
    """按配置返回 display 要下发的图 URL 列表：
    人物随机开 = 全部候选图；关 = 只下发固定的那张。磁盘上已消失的自动剔除。"""
    urls = [file_url(f) for f in (enc.get("imgs") or [])]
    urls = [u for u in urls if u]
    if not enc.get("rand_char"):
        fixed = file_url(enc.get("img"))
        if fixed:
            return [fixed]
        return urls[:1]
    return urls


def new_filename():
    """生成不冲突的图文件名（char_<毫秒时间戳>.png）。"""
    while True:
        fname = "char_%d.png" % int(time.time() * 1000)
        if not os.path.exists(file_path(fname)):
            return fname
        time.sleep(0.002)


def dark_border_ratio(im):
    """四边像素的暗色占比（灰度 <120）：占比高 = 上传图自带深色背景（黑底被固化的
    那种），墨水屏上会渲染成黑块。只做检测提示、不自动修改——服务器分不清
    哪是背景哪是人物，改了反而毁图。采样四边（各 ~40 点），永不抛异常。"""
    try:
        g = im if im.mode == "L" else im.convert("L")
        w, h = g.size
        px = g.load()
        dark = total = 0
        for x in range(0, w, max(1, w // 40)):
            for y in (0, h - 1):
                total += 1
                if px[x, y] < 120:
                    dark += 1
        for y in range(0, h, max(1, h // 40)):
            for x in (0, w - 1):
                total += 1
                if px[x, y] < 120:
                    dark += 1
        return dark / float(total) if total else 0.0
    except Exception:
        return 0.0


def save_upload(raw):
    """上传的图片字节 -> 透明底合成白底 + 灰度 + 缩放后存为 char_<ts>.png。
    成功返回 (文件名, 背景偏暗警示 bool)：警示=True 表示图自带深色背景，墨水屏上
    会显示为黑底块，admin 弹提示建议换白底/透明底版本（不阻断，用户可自担）。
    文件不是图片 / 超 5MB / 缺 Pillow 且原图 >500KB 抛 ValueError。"""
    if len(raw) > ENC_IMG_MAX_UPLOAD:
        raise ValueError(u"图片超过 5MB 上限")
    os.makedirs(ENC_DIR, exist_ok=True)
    fname = new_filename()
    try:
        from PIL import Image
        try:
            im = Image.open(io.BytesIO(raw))
            im.load()
        except Exception as e:
            raise ValueError(u"不是有效的图片文件（%s）" % e)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)      # 透明底合成白底：否则透明区按 RGB(0,0,0) 变黑块
            im = bg
        dark = dark_border_ratio(im) > 0.5   # 合成白底后判（透明图的角落此时已是白）
        im = im.convert("L")            # 彩色卡通在墨水屏上发灰：统一转灰度（保细节）
        if im.height > ENC_IMG_HEIGHT:
            w = max(1, int(round(im.width * ENC_IMG_HEIGHT / float(im.height))))
            im = im.resize((w, ENC_IMG_HEIGHT), Image.LANCZOS)
        im.save(file_path(fname), "PNG")
        return fname, dark
    except ImportError:
        # Pillow 是可选依赖（与 emoji 管线同款降级）：原样存，交给浏览器渲染
        if len(raw) > 500 * 1024:
            raise ValueError(u"服务器未安装 Pillow，原图需 ≤500KB（建议装上 Pillow 自动转灰度）")
        with open(file_path(fname), "wb") as f:
            f.write(raw)
        return fname, False


def delete_file(fname):
    """删除一张人物图文件（磁盘）；data.json 的列表由调用方维护。"""
    try:
        os.unlink(file_path(fname))
        return True
    except OSError:
        return False
