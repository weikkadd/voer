#!/usr/bin/env python3
"""
Voer.host 免费服务器会话续期（Playwright 版）

原理：免费档会话制（默认 4h），续期需看完 3 个 Google 激励广告 -> +4h。
按钮在跨进程 iframe（wormies.voer.host / googleads.g.doubleclick.net）里，
必须用 Playwright（原生支持 OOPIF）才能点到，Selenium/JS 无法穿透。

限制：每 UTC 日最多 4 次、每会话最多 4 次（每次 +4h）。

优先读取环境变量（适合 GitHub Actions / Docker / cron）：
    VOER_SERVER_ID   服务器 UUID
    VOER_TOKEN       Cookie 里的 token（JWT）

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
import base64

BASE = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)

DEFAULT_CONFIG = {
    "server_id": "在这里填服务器 UUID（面板地址 /panel/server/ 后面那串）",
    "token": "在这里填浏览器 Cookie 里 voer.host 的 token 值（JWT）",
    "ads_per_extension": 3,  # 每次续期需要的广告数
    "ad_duration_sec": 32,  # 单个广告播放时长，按实际适当加大
    "headless": False,  # 必须为 False，headless 下广告不会发奖励
    "use_system_chrome": False,  # True = 用系统 Chrome；False = 用 playwright 自带 chromium
}

from playwright.sync_api import sync_playwright


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def _jwt_hint(token: str) -> str:
    """返回 token 的安全提示信息（不泄露完整内容）。"""
    t = (token or "").strip()
    if not t:
        return "空"
    parts = t.split(".")
    hint = f"长度={len(t)}, 段数={len(parts)}, 开头={t[:8]}..., 结尾=...{t[-6:]}"
    if len(parts) != 3:
        hint += "  【警告：标准 JWT 应有 3 段用 . 分隔，可能复制不完整】"
    if not t.startswith("eyJ"):
        hint += "  【警告：正常 JWT 一般以 eyJ 开头】"
    # 尝试解析 payload 里的 exp
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
    """优先读环境变量，再合并 config.json（如果存在）。"""
    cfg = dict(DEFAULT_CONFIG)

    # 1. 本地 config.json（可选）
    if CONFIG_PATH.exists():
        try:
            file_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg.update(file_cfg)
        except Exception as e:
            log(f"读取 config.json 失败: {e}")

    # 2. 环境变量覆盖（最高优先级，适合 CI / Docker）
    # 注意：去掉首尾空白和可能的引号
    env_sid = os.environ.get("VOER_SERVER_ID", "").strip().strip('"').strip("'")
    env_token = os.environ.get("VOER_TOKEN", "").strip().strip('"').strip("'")
    if env_sid:
        cfg["server_id"] = env_sid
    if env_token:
        cfg["token"] = env_token

    # 可选覆盖数字配置
    if os.environ.get("VOER_ADS_PER_EXTENSION"):
        cfg["ads_per_extension"] = int(os.environ["VOER_ADS_PER_EXTENSION"])
    if os.environ.get("VOER_AD_DURATION_SEC"):
        cfg["ad_duration_sec"] = int(os.environ["VOER_AD_DURATION_SEC"])

    # 检查必填项
    sid = cfg.get("server_id", "")
    token = cfg.get("token", "")
    if (
        not sid
        or "在这里填" in sid
        or not token
        or "在这里填" in token
    ):
        log("=" * 60)
        log("缺少必要配置！请设置以下任一方式：")
        log("")
        log("【推荐】环境变量（GitHub Actions / Docker / 系统环境）：")
        log("  export VOER_SERVER_ID='你的服务器UUID'")
        log("  export VOER_TOKEN='你的JWT token'")
        log("")
        log("【本地】复制 config.example.json 为 config.json 并填写")
        log("  cp config.example.json config.json")
        log("")
        log("如何获取值：")
        log("  1. server_id = 浏览器打开面板后，地址栏")
        log("     https://voer.host/panel/server/ 后面那一串 UUID")
        log("  2. token = 浏览器 F12 → Application(应用) → Cookies")
        log("     → 选 voer.host → 找到 name=token 的值（以 eyJ 开头的 JWT）")
        log("     大约 7 天过期，过期后重新复制即可")
        log("=" * 60)
        sys.exit(1)

    # 打印安全诊断信息（不泄露完整 token）
    log(f"server_id 长度={len(sid)}, 开头={sid[:8]}...")
    log(f"token 诊断: {_jwt_hint(token)}")
    if os.environ.get("VOER_SERVER_ID"):
        log("配置来源: 环境变量 VOER_SERVER_ID / VOER_TOKEN")
    elif CONFIG_PATH.exists():
        log("配置来源: 本地 config.json")

    return cfg


def api_state(cfg):
    """调用 API 获取服务器状态，失败时给出明确原因。"""
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
            log("【401/403 常见原因】")
            log("  1. VOER_TOKEN 已过期（约 7 天）→ 重新从浏览器复制")
            log("  2. token 复制不完整（JWT 必须有三段：xxxxx.yyyyy.zzzzz）")
            log("  3. Secret 里多了空格、换行或引号")
            log("  4. 复制了错误的 Cookie（必须是 name=token 那一项）")
            log("")
            log("【正确重新获取 token 步骤】")
            log("  ① 浏览器打开 https://voer.host 并登录")
            log("  ② 按 F12 → Application（应用）→ Cookies → voer.host")
            log("  ③ 找到 Name = token 的行，完整复制 Value（以 eyJ 开头）")
            log("  ④ GitHub: Settings → Secrets → 更新 VOER_TOKEN")
            log("     本地: 更新环境变量或 config.json")
            log("  ⑤ 再跑一次 --status 验证")
            log("")
            log(f"当前 token 诊断: {_jwt_hint(cfg['token'])}")
        elif e.code == 404:
            log("【404】server_id 可能写错，请检查面板地址栏里的 UUID")
        log("=" * 60)
        raise SystemExit(1) from e
    except urllib.error.URLError as e:
        log(f"网络错误: {e.reason}")
        raise SystemExit(1) from e


def click_anywhere(page, texts, timeout_ms, exact=True):
    """在所有 frame（含跨进程 iframe）里找文本并真实点击"""
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


def dump_page_debug(page, tag="debug"):
    """失败时打印页面上可见按钮/链接文字，便于排查文案变化"""
    log(f"----- 页面诊断 ({tag}) -----")
    log(f"URL: {page.url}")
    try:
        title = page.title()
        log(f"Title: {title}")
    except Exception:
        pass
    texts = []
    try:
        for frame in page.frames:
            for role in ("button", "link"):
                try:
                    locs = frame.get_by_role(role).all()
                    for loc in locs[:40]:
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
    # 尝试截图（CI 里可在后续步骤上传）
    try:
        shot = pathlib.Path("debug_screenshot.png")
        page.screenshot(path=str(shot), full_page=True)
        log(f"已保存截图: {shot.resolve()}")
    except Exception as e:
        log(f"截图失败: {e}")
    log("----- 诊断结束 -----")


def main():
    cfg = load_config()
    server_id = cfg["server_id"]
    url = f"https://voer.host/panel/server/{server_id}"

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
        return

    with sync_playwright() as p:
        launch = dict(
            headless=cfg["headless"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1400,1000",
                "--no-sandbox",  # CI / Docker 常需要
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

        # 关闭可能的 Cookie/同意弹窗
        for accept_txt in ("Accept", "Accept all", "同意", "接受", "I agree", "OK"):
            hit = click_anywhere(page, [accept_txt], 3000)
            if hit:
                log(f"已点同意弹窗: {hit}")
                break

        # 续期入口按钮：兼容简中/繁中/英文等多种文案
        extend_labels = [
            "延伸", "延长", "延長", "续期", "續期",
            "Extend", "Extend session", "Extend Session",
            "Renew", "Watch ads", "Watch Ads",
        ]
        log("正在寻找「续期/延伸」按钮…")
        hit = click_anywhere(page, extend_labels, 45000)
        if not hit:
            # 再等一会儿，页面可能还在加载
            page.wait_for_timeout(5000)
            hit = click_anywhere(page, extend_labels, 30000, exact=False)
        if not hit:
            log("未找到续期入口按钮")
            dump_page_debug(page, "找不到延伸按钮")
            raise SystemExit(2)
        log(f"已点击续期入口: {hit}")
        page.wait_for_timeout(3000)

        # 观看广告确认按钮
        watch_labels = [
            "觀看廣告", "观看广告", "观看广告", "觀看廣告",
            "Watch ad", "Watch Ad", "Watch ads", "Watch Ads",
            "Watch", "开始", "開始",
        ]
        hit2 = click_anywhere(page, watch_labels, 30000)
        if not hit2:
            hit2 = click_anywhere(page, watch_labels, 20000, exact=False)
        if not hit2:
            log("未找到「观看广告」按钮（可能已直接进入广告流程）")
            dump_page_debug(page, "找不到观看广告按钮")
        else:
            log(f"已点击观看广告: {hit2}")
        log("已打开广告流程，等待 Ad ready…")
        page.wait_for_timeout(8000)

        total = int(cfg["ads_per_extension"])
        for i in range(1, total + 1):
            hit = click_anywhere(page, ["Watch ad", "觀看廣告", "观看广告"], 75000)
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
                now.get("sessionExtensions", 0) > before.get("sessionExtensions", 0)
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
                break
            time.sleep(10)
        else:
            log("未检测到续期生效，请检查窗口是否卡在某个广告上，或今日次数已用尽（最多 4 次）")

        page.wait_for_timeout(3000)
        browser.close()


if __name__ == "__main__":
    main()
