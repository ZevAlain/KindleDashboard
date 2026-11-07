# -*- coding: utf-8 -*-
"""密钥解析：管理密码 / session 密钥 / 看板免登录 key（三个独立密钥）。

优先级（各自独立）：命令行 > 环境变量 > .runtime_secrets 存值 > 自动生成并写入。

为什么看板 key 要与管理密码**分离**：?key= 免登录链接是泄漏面最大的凭据
（Kindle 浏览器历史/书签/服务器日志/Referer 都会留 URL）。旧版它就是管理密码
本身——拿到 key 的人去 /login 输入同一串就能登进管理页（那里能看到 AI 的
API Key 等敏感配置）。分离后：看板 key 泄漏只损失"看板+勾选"，管理页无恙。
"""
import argparse
import json
import os
import secrets as _secrets

from .config import RUNTIME_SECRETS_FILE


def _parse_secret_args():
    """读取 --password / --secret-key / --display-key；其余命令行参数一律忽略。"""
    try:
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--password", dest="password")
        p.add_argument("--secret-key", dest="secret_key")
        p.add_argument("--display-key", dest="display_key")
        args, _ = p.parse_known_args()
        return (args.password, args.secret_key, args.display_key)
    except Exception:
        return (None, None, None)


def _read_stored():
    try:
        with open(RUNTIME_SECRETS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_stored(stored):
    """整份密钥文件落盘（tmp + 0600 + os.replace 原子替换）。失败打印警告不抛异常。"""
    try:
        tmp = RUNTIME_SECRETS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stored, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, RUNTIME_SECRETS_FILE)
        return True
    except Exception as e:
        print("[安全] 警告：无法写入 %s，本次生成的密钥重启后会变：%s"
              % (os.path.basename(RUNTIME_SECRETS_FILE), e))
        return False


def _resolve_secrets():
    """返回 (管理密码, session密钥, 看板key, 密码来源, 密钥来源, key来源)。永不抛异常。
    多 worker 并发首启：整份文件 O_EXCL 原子创建，只有一个进程赢；升级场景（文件
    已存在但缺 display_key）补写后以文件为准，保证重启后 key 不变。"""
    vals = {
        "password": ((os.environ.get("KD_PASSWORD") or "").strip(), "环境变量"),
        "secret_key": ((os.environ.get("KD_SECRET_KEY") or "").strip(), "环境变量"),
        "display_key": ((os.environ.get("KD_DISPLAY_KEY") or "").strip(), "环境变量"),
    }
    cli_pwd, cli_key, cli_dkey = _parse_secret_args()
    for cli, k in ((cli_pwd, "password"), (cli_key, "secret_key"), (cli_dkey, "display_key")):
        if cli and cli.strip():
            vals[k] = (cli.strip(), "命令行")
    stored = _read_stored()
    for k in list(vals):
        if not vals[k][0]:
            v = str(stored.get(k) or "").strip()
            if v:
                vals[k] = (v, "本地生成")

    need_gen = [k for k in ("password", "secret_key", "display_key") if not vals[k][0]]
    if need_gen:
        for k in need_gen:
            stored[k] = (_secrets.token_hex(32) if k == "secret_key"
                         else _secrets.token_urlsafe(16))
            vals[k] = (stored[k], "本地生成")
        try:
            # O_CREAT|O_EXCL 原子创建（0600 一步到位）：全新部署时多 worker 只有一个赢
            fd = os.open(RUNTIME_SECRETS_FILE,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(stored, f, ensure_ascii=False, indent=2)
            print("[安全] 已生成并写入 %s（%s）"
                  % (os.path.basename(RUNTIME_SECRETS_FILE),
                     "/".join(need_gen)))
        except FileExistsError:
            # 文件已存在但缺新密钥（升级）或并发者已写入：以文件里的为准；
            # 文件里也缺的（升级场景）把本次生成的补写进去，保证重启后不变
            disk = _read_stored()
            patch = {}
            for k in need_gen:
                v = str(disk.get(k) or "").strip()
                if v:
                    vals[k] = (v, "本地生成")
                else:
                    patch[k] = stored[k]
            if patch:
                disk.update(patch)
                if _write_stored(disk):
                    print("[安全] 已为 %s 补写新密钥并保持其余不变：%s"
                          % (os.path.basename(RUNTIME_SECRETS_FILE), "/".join(patch)))
        except Exception as e:
            print("[安全] 警告：无法写入 .runtime_secrets，本次密钥重启后会变：%s" % e)
    return (vals["password"][0], vals["secret_key"][0], vals["display_key"][0],
            vals["password"][1], vals["secret_key"][1], vals["display_key"][1])


(SECRET_PASSWORD, SECRET_KEY, SECRET_DISPLAY_KEY,
 SECRET_PASSWORD_SRC, SECRET_KEY_SRC, SECRET_DISPLAY_KEY_SRC) = _resolve_secrets()
