#!/usr/bin/env python3
"""
Voer.host 免费服务器会话续期（Playwright 版）

原理：免费档会话制（默认 4h），续期需看完 3 个 Google 激励广告 -> +4h。
按钮在跨进程 iframe（wormies.voer.host / googleads.g.doubleclick.net）里，
必须用 Playwright（原生支持 OOPIF）才能点到，Selenium/JS 无法穿透。

限制：每 UTC 日最多 4 次、每会话最多 4 次（每次 +4h）。

优先读取环境变量（适合 GitHub Actions / Docker / cron）：
    VOER_SERVER_ID        服务器 UUID（必须）
    VOER_TOKEN            Cookie 里的 token JWT（必须）
    TELEGRAM_BOT_TOKEN    Telegram Bot Token（可选，用于通知）
    TELEGRAM_CHAT_ID      Telegram Chat ID（可选，用于通知）

也支持本地 config.json（环境变量优先级更高）。

VPS / CI 无图形界面时必须用虚拟显示：
    xvfb-run -a python3 voer_renew.py

用法：
    python3 voer_renew.py            自动续期一次（3 个广告，约 3 分钟）
    python3 voer_renew.py --status   只看当前状态，不看广告
"""
import json
import os
import sys
import time
import pathlib
import urllib.request
import urllib.error
import urllib.parse
import base64
import mimetypes

BASE = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)

DEFAULT_CONFIG = {
    "server_id": "在这里填服务器 UUID（面板地址 /panel/server/ 后面那串）",
    "token": "在这里填浏览器 Cookie 里 voer.host 的 token 值（JWT）",
    "ads_per_extension": 3,
    "ad_duration_sec": 32,
    "headless": False,
    "use_system_chrome": False,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
}

from playwright.sync_api import sync_playwright


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


# ---------------------------------------------------------------------------
# Telegram 通知
# ---------------------------------------------------------------------------
def _tg_enabled(cfg) -> bool:
    return bool(cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"))


def tg_send_message(cfg, text: str) -> bool:
    """发送纯文本消息到 Telegram。"""
    if not _tg_enabled(cfg):
        return False
    token = cfg["telegram_bot_token"]
    chat_id = cfg["telegram_chat_id"]
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode()
    req = urllib.request.Request(
        api,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
            if data.get("ok"):
                log("Telegram 文本通知已发送")
                return True
            log(f"Telegram 发送失败: {data}")
            return False
    except Exception as e:
        log(f"Telegram 发送异常: {e}")
        return False


def tg_send_photo(cfg, photo_path: pathlib.Path, caption: str = "") -> bool:
    """发送图片（截图）到 Telegram。"""
    if not _tg_enabled(cfg):
        return False
    if not photo_path.exists():
        log(f"截图不存在，跳过发图: {photo_path}")
        return False
    token = cfg["telegram_bot_token"]
    chat_id = str(cfg["telegram_chat_id"])
    api = f"https://api.telegram.org/bot{token}/sendPhoto"

    boundary = f"----VoerBoundary{int(time.time())}"
    filename = photo_path.name
    file_data = photo_path.read_bytes()
    mime = mimetypes.guess_type(filename)[0] or "image/png"

    parts = []
    # chat_id
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{chat_id}\r\n".encode()
    )
    # caption
    if caption:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n'
            f"{caption}\r\n".encode()
        )
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="parse_mode"\r\n\r\n'
            f"HTML\r\n".encode()
        )
    # photo
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode()
        + file_data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        api,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
            if data.get("ok"):
                log("Telegram 截图已发送")
                return True
            log(f"Telegram 发图失败: {data}")
            return False
    except Exception as e:
        log(f"Telegram 发图异常: {e}")
        return False


def notify(cfg, title: str, lines: list, photo: pathlib.Path | None = None):
    """统一通知入口：有 TG 配置就发，没有就只打日志。"""
    text = f"<b>{title}</b>\n" + "\n".join(lines)
    log("通知内容:\n" + text.replace("<b>", "").replace("</b>", ""))
    if not _tg_enabled(cfg):
        log("未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，跳过 TG 通知")
        return
    if photo and photo.exists():
        # 图片 caption 最长约 1024，超长则先发图再发文字
        if len(text) <= 1000:
            tg_send_photo(cfg, photo, caption=text)
        else:
            tg_send_photo(cfg, photo, caption=title)
            tg_send_message(cfg, text)
    else:
        tg_send_message(cfg, text)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def _jwt_hint(token: str) -> str:
    t = (token or "").strip()
    if not t:
        return "空"
    parts = t.split(".")
    hint = f"长度={len(t)}, 段数={len(parts)}, 开头={t[:8]}..., 结尾=...{t[-6:]}"
    if len(parts) != 3:
        hint += "  【警告：标准 JWT 应有 3 段用 . 分隔，可能复制不完整】"
    if not t.startswith("eyJ"):
        hint += "  【警告：正常 JWT 一般以 eyJ 开头】"
    try:
        if len(parts) >= 2:
            pad = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(pad))
            exp = payload.get("exp")
            if exp:
                import datetime

                exp_dt = datetime.datetime.utcfromtimestamp(exp)
                now = datetime.datetime.utcnow()
                if exp_dt < now:
                    hint += f"  【已过期！过期时间 UTC {exp_dt.isoformat()}Z】"
                else:
                    left = exp_dt - now
                    hours = int(left.total_seconds() // 3600)
                    hint += f"  【未过期，剩余约 {hours} 小时，过期 UTC {exp_dt.isoformat()}Z】"
    except Exception:
        pass
    return hint


def load_config():
    cfg = dict(DEFAULT_CONFIG)

    if CONFIG_PATH.exists():
        try:
            file_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg.update(file_cfg)
        except Exception as e:
            log(f"读取 config.json 失败: {e}")

    env_sid = os.environ.get("VOER_SERVER_ID", "").strip().strip('"').strip("'")
    env_token = os.environ.get("VOER_TOKEN", "").strip().strip('"').strip("'")
    if env_sid:
        cfg["server_id"] = env_sid
    if env_token:
        cfg["token"] = env_token

    # Telegram（环境变量优先）
    env_tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().strip('"').strip("'")
    env_tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")
    if env_tg_token:
        cfg["telegram_bot_token"] = env_tg_token
    if env_tg_chat:
        cfg["telegram_chat_id"] = env_tg_chat

    if os.environ.get("VOER_ADS_PER_EXTENSION"):
        cfg["ads_per_extension"] = int(os.environ["VOER_ADS_PER_EXTENSION"])
    if os.environ.get("VOER_AD_DURATION_SEC"):
        cfg["ad_duration_sec"] = int(os.environ["VOER_AD_DURATION_SEC"])

    sid = cfg.get("server_id", "")
    token = cfg.get("token", "")
    if not sid or "在这里填" in sid or not token or "在这里填" in token:
        log("=" * 60)
        log("缺少必要配置！请设置 VOER_SERVER_ID 和 VOER_TOKEN")
        log("可选：TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID 用于通知")
        log("=" * 60)
        sys.exit(1)

    log(f"server_id 长度={len(sid)}, 开头={sid[:8]}...")
    log(f"token 诊断: {_jwt_hint(token)}")
    if _tg_enabled(cfg):
        log("Telegram 通知: 已启用")
    else:
        log("Telegram 通知: 未配置（设置 TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID 可开启）")
    if os.environ.get("VOER_SERVER_ID"):
        log("配置来源: 环境变量")
    elif CONFIG_PATH.exists():
        log("配置来源: 本地 config.json")

    return cfg


def api_state(cfg):
    url = f"https://voer.host/api/servers/{cfg['server_id']}"
    req = urllib.request.Request(
        url,
        headers={
            "Cookie": f"token={cfg['token']}",
            "User-Agent": UA,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
            if "server" not in data:
                raise RuntimeError(f"API 返回格式异常: {list(data.keys())}")
            return data["server"]
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="replace")[:300]
        except Exception:
            pass
        log("=" * 60)
        log(f"API 请求失败: HTTP {e.code} {e.reason}")
        log(f"请求地址: {url}")
        if body:
            log(f"响应内容: {body}")
        if e.code in (401, 403):
            log("")
            log("【401/403】token 过期或错误，请重新从浏览器复制 VOER_TOKEN")
            log(f"当前 token 诊断: {_jwt_hint(cfg['token'])}")
        log("=" * 60)
        raise SystemExit(1) from e
    except urllib.error.URLError as e:
        log(f"网络错误: {e.reason}")
        raise SystemExit(1) from e


def click_anywhere(page, texts, timeout_ms, exact=True):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for frame in page.frames:
            for t in texts:
                makers = [
                    lambda t=t, f=frame: f.get_by_role("button", name=t, exact=exact).first,
                    lambda t=t, f=frame: f.get_by_text(t, exact=exact).first,
                    lambda t=t, f=frame: f.locator(f"button:has-text('{t}')").first,
                    lambda t=t, f=frame: f.locator(f"[role=button]:has-text('{t}')").first,
                ]
                for maker in makers:
                    try:
                        loc = maker()
                        if loc.count() and loc.is_visible():
                            loc.click(timeout=3000)
                            return f"{t}@{frame.url[:60]}"
                    except Exception:
                        pass
        time.sleep(1.2)
    return None


def take_screenshot(page, name="screenshot.png") -> pathlib.Path:
    path = pathlib.Path(name)
    try:
        page.screenshot(path=str(path), full_page=True)
        log(f"截图已保存: {path.resolve()}")
    except Exception as e:
        log(f"截图失败: {e}")
    return path


def dump_page_debug(page, tag="debug"):
    log(f"----- 页面诊断 ({tag}) -----")
    log(f"URL: {page.url}")
    try:
        log(f"Title: {page.title()}")
    except Exception:
        pass
    texts = []
    try:
        for frame in page.frames:
            for role in ("button", "link"):
                try:
                    for loc in frame.get_by_role(role).all()[:40]:
                        try:
                            if loc.is_visible():
                                t = (loc.inner_text(timeout=500) or "").strip()
                                if t and t not in texts:
                                    texts.append(t)
                        except Exception:
                            pass
                except Exception:
                    pass
    except Exception as e:
        log(f"收集按钮失败: {e}")
    if texts:
        log("可见按钮/链接文字:")
        for t in texts[:50]:
            log(f"  - {t!r}")
    else:
        log("未收集到可见按钮文字")
    take_screenshot(page, "debug_screenshot.png")
    log("----- 诊断结束 -----")


def main():
    cfg = load_config()
    server_id = cfg["server_id"]
    url = f"https://voer.host/panel/server/{server_id}"
    short_id = server_id[:8] + "…"

    if "--status" in sys.argv:
        s = api_state(cfg)
        for k in (
            "status",
            "sessionExpiresAt",
            "sessionExtensions",
            "sessionExtensionsToday",
            "sessionDuration",
            "adsWatched",
        ):
            print(f"{k} = {s.get(k)}")
        # status 也可发一条简短通知（可选）
        if os.environ.get("TG_NOTIFY_STATUS") == "1":
            notify(
                cfg,
                "📊 Voer 状态查询",
                [
                    f"服务器: <code>{short_id}</code>",
                    f"状态: {s.get('status')}",
                    f"到期: {s.get('sessionExpiresAt')}",
                    f"累计续期: {s.get('sessionExtensions')}",
                    f"今日续期: {s.get('sessionExtensionsToday')}",
                ],
            )
        return

    success = False
    before = {}
    now = {}
    shot = pathlib.Path("renew_screenshot.png")

    with sync_playwright() as p:
        launch = dict(
            headless=cfg["headless"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1400,1000",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        if cfg["use_system_chrome"]:
            launch["channel"] = "chrome"
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        ctx.add_cookies(
            [
                {
                    "name": "token",
                    "value": cfg["token"],
                    "domain": "voer.host",
                    "path": "/",
                    "secure": True,
                }
            ]
        )
        page = ctx.new_page()
        try:
            log(f"打开页面: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(5000)

            before = api_state(cfg)
            log(
                "当前到期:",
                before.get("sessionExpiresAt"),
                "| 已续期:",
                before.get("sessionExtensions"),
                "| 今日:",
                before.get("sessionExtensionsToday"),
            )

            for accept_txt in ("Accept", "Accept all", "同意", "接受", "I agree", "OK"):
                hit = click_anywhere(page, [accept_txt], 3000)
                if hit:
                    log(f"已点同意弹窗: {hit}")
                    break

            extend_labels = [
                "延伸",
                "延长",
                "延長",
                "续期",
                "續期",
                "Extend",
                "Extend session",
                "Extend Session",
                "Renew",
                "Watch ads",
                "Watch Ads",
            ]
            log("正在寻找「续期/延伸」按钮…")
            hit = click_anywhere(page, extend_labels, 45000)
            if not hit:
                page.wait_for_timeout(5000)
                hit = click_anywhere(page, extend_labels, 30000, exact=False)
            if not hit:
                log("未找到续期入口按钮")
                dump_page_debug(page, "找不到延伸按钮")
                notify(
                    cfg,
                    "❌ Voer 续期失败",
                    [
                        f"服务器: <code>{short_id}</code>",
                        "原因: 未找到「延伸/续期」按钮",
                        "请查看 Actions 日志或 debug 截图",
                    ],
                    photo=pathlib.Path("debug_screenshot.png"),
                )
                raise SystemExit(2)
            log(f"已点击续期入口: {hit}")
            page.wait_for_timeout(3000)

            watch_labels = [
                "觀看廣告",
                "观看广告",
                "Watch ad",
                "Watch Ad",
                "Watch ads",
                "Watch Ads",
                "Watch",
                "开始",
                "開始",
            ]
            hit2 = click_anywhere(page, watch_labels, 30000)
            if not hit2:
                hit2 = click_anywhere(page, watch_labels, 20000, exact=False)
            if not hit2:
                log("未找到「观看广告」按钮（可能已直接进入广告流程）")
            else:
                log(f"已点击观看广告: {hit2}")
            log("已打开广告流程，等待 Ad ready…")
            page.wait_for_timeout(8000)

            total = int(cfg["ads_per_extension"])
            for i in range(1, total + 1):
                hit = click_anywhere(
                    page, ["Watch ad", "觀看廣告", "观看广告"], 75000
                )
                if not hit:
                    log(f"第 {i} 个 Watch ad 未找到，停止")
                    break
                log(f"已点击第 {i}/{total} 个 Watch ad（{hit}），播放中…")
                page.wait_for_timeout(int(cfg["ad_duration_sec"]) * 1000)
                closed = click_anywhere(page, ["Close", "關閉", "关闭"], 60000)
                log(
                    f"第 {i} 个广告:",
                    f"已关闭（{closed}）" if closed else "未找到 Close（可能自动关闭）",
                )
                page.wait_for_timeout(6000)

            end = time.time() + 180
            while time.time() < end:
                try:
                    now = api_state(cfg)
                except SystemExit:
                    now = None
                except Exception:
                    now = None
                if now and (
                    now.get("sessionExtensions", 0)
                    > before.get("sessionExtensions", 0)
                    or now.get("sessionExpiresAt") != before.get("sessionExpiresAt")
                ):
                    log(
                        "续期成功 -> 新到期:",
                        now.get("sessionExpiresAt"),
                        "| 累计:",
                        now.get("sessionExtensions"),
                        "| 今日:",
                        now.get("sessionExtensionsToday"),
                    )
                    success = True
                    break
                time.sleep(10)
            else:
                log(
                    "未检测到续期生效，请检查窗口是否卡在某个广告上，或今日次数已用尽（最多 4 次）"
                )
                now = now or {}

            # 成功/失败都截一张最终画面
            shot = take_screenshot(page, "renew_screenshot.png")

        except SystemExit:
            raise
        except Exception as e:
            log(f"运行异常: {e}")
            try:
                dump_page_debug(page, "异常")
            except Exception:
                pass
            notify(
                cfg,
                "❌ Voer 续期异常",
                [
                    f"服务器: <code>{short_id}</code>",
                    f"错误: <code>{e}</code>",
                ],
                photo=pathlib.Path("debug_screenshot.png"),
            )
            raise
        finally:
            page.wait_for_timeout(1500)
            browser.close()

    # 结束后发通知
    if success:
        notify(
            cfg,
            "✅ Voer 续期成功",
            [
                f"服务器: <code>{short_id}</code>",
                f"原到期: {before.get('sessionExpiresAt')}",
                f"新到期: <b>{now.get('sessionExpiresAt')}</b>",
                f"累计续期: {now.get('sessionExtensions')}",
                f"今日续期: {now.get('sessionExtensionsToday')}",
            ],
            photo=shot,
        )
    else:
        notify(
            cfg,
            "⚠️ Voer 续期未生效",
            [
                f"服务器: <code>{short_id}</code>",
                f"当前到期: {before.get('sessionExpiresAt')}",
                f"累计: {before.get('sessionExtensions')} | 今日: {before.get('sessionExtensionsToday')}",
                "可能原因: 广告未播完 / 今日已达 4 次上限 / 页面卡住",
            ],
            photo=shot if shot.exists() else None,
        )
        raise SystemExit(3)


if __name__ == "__main__":
    main()
