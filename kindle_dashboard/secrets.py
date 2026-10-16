# -*- coding: utf-8 -*-
"""登录密码与 session 密钥的解析（命令行 > 环境变量 > 本地生成 .runtime_secrets）。"""
import argparse
import json
import os
import secrets as _secrets

from .config import RUNTIME_SECRETS_FILE

# ----------------- 安全配置（参数传入，不再写死在源码里） -----------------
# 优先级：命令行参数 > 环境变量 > 本地生成
#   命令行：    python app.py --password=你的密码 --secret-key=固定密钥
#   环境变量：  KD_PASSWORD / KD_SECRET_KEY
# 都不给时：首次运行自动生成并写入 .runtime_secrets（0600 权限），
#           这样重启后密码不变、Kindle 上 ?key= 免登录地址与已登录状态也都保持有效。


def _parse_secret_args():
    """读取 --password / --secret-key；其余命令行参数一律忽略（不影响 flask/gunicorn 启动）。"""
    try:
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--password", dest="password")
        p.add_argument("--secret-key", dest="secret_key")
        args, _ = p.parse_known_args()
        return (args.password, args.secret_key)
    except SystemExit:
        return (None, None)
    except Exception:
        return (None, None)


def _resolve_secrets():
    """返回 (登录密码, session密钥, 密码来源, 密钥来源)。永不抛异常。"""
    password = (os.environ.get("KD_PASSWORD") or "").strip()
    key = (os.environ.get("KD_SECRET_KEY") or "").strip()
    src_pwd = "命令行/环境变量" if password else ""
    src_key = "命令行/环境变量" if key else ""

    cli_pwd, cli_key = _parse_secret_args()
    if cli_pwd and cli_pwd.strip():
        password, src_pwd = cli_pwd.strip(), "命令行"
    if cli_key and cli_key.strip():
        key, src_key = cli_key.strip(), "命令行"

    stored = {}
    try:
        with open(RUNTIME_SECRETS_FILE, "r", encoding="utf-8") as f:
            stored = json.load(f)
    except Exception:
        stored = {}

    generated = False
    if not password:
        password = str(stored.get("password") or "").strip()
        src_pwd = "本地生成" if password else ""
    if not key:
        key = str(stored.get("secret_key") or "").strip()
        src_key = "本地生成" if key else ""
    if not password:
        password = _secrets.token_urlsafe(12)
        stored["password"] = password
        src_pwd, generated = "本地生成", True
    if not key:
        key = _secrets.token_hex(32)
        stored["secret_key"] = key
        src_key, generated = "本地生成", True

    if generated:
        try:
            # O_CREAT|O_EXCL 原子创建（0600 一步到位，无权限窗口）：
            # gunicorn 多 worker 同时首次启动时，只有一个进程抢到创建权，
            # 其余进程重读文件拿到同一份密钥——否则各 worker 的 secret_key 不一致，
            # session 会跨 worker 互不认可、登录态随机失效。
            fd = os.open(RUNTIME_SECRETS_FILE,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(stored, f, ensure_ascii=False, indent=2)
            print("[安全] 已生成并写入 %s（来源：%s）"
                  % (os.path.basename(RUNTIME_SECRETS_FILE), src_pwd))
        except FileExistsError:
            # 并发者已创建：以文件里的为准（本进程生成的作废）
            try:
                with open(RUNTIME_SECRETS_FILE, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                pw = str(stored.get("password") or "").strip()
                sk = str(stored.get("secret_key") or "").strip()
                if pw and sk:
                    password, key = pw, sk
                    src_pwd, src_key = "本地生成", "本地生成"
            except Exception:
                pass
        except Exception as e:
            print("[安全] 警告：无法写入 .runtime_secrets，本次密码重启后会变：%s" % e)
    return password, key, src_pwd, src_key


SECRET_PASSWORD, SECRET_KEY, SECRET_PASSWORD_SRC, SECRET_KEY_SRC = _resolve_secrets()
