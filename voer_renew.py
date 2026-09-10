#!/usr/bin/env python3
"""
Voer.host 免费服务器会话续期（Playwright 版，适用于 VPS）

原理：免费档会话制（默认 4h），续期需看完 3 个 Google 激励广告 -> +4h。
按钮在跨进程 iframe（wormies.voer.host / googleads.g.doubleclick.net）里，
必须用 Playwright（原生支持 OOPIF）才能点到，Selenium/JS 无法穿透。

限制：每 UTC 日最多 4 次、每会话最多 4 次（每次 +4h）。

VPS 无图形界面的运行方式（必须有虚拟显示，否则广告不会播）：
    xvfb-run -a python3 voer_renew.py

用法：
    python3 voer_renew.py            自动续期一次（3 个广告，约 3 分钟）
    python3 voer_renew.py --status   只看当前状态，不看广告
"""
import json, os, sys, time, pathlib, urllib.request

BASE = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

DEFAULT_CONFIG = {
    "server_id": "在这里填服务器 UUID（面板地址 /panel/server/ 后面那串）",
    "token": "在这里填浏览器 Cookie 里 voer.host 的 token 值（JWT）",
    "ads_per_extension": 3,      # 每次续期需要的广告数
    "ad_duration_sec": 32,       # 单个广告播放时长，按实际适当加大
    "headless": False,           # 必须为 False，headless 下广告不会发奖励
    "use_system_chrome": False,  # True = 用系统 Chrome；False = 用 playwright 自带 chromium
}

from playwright.sync_api import sync_playwright


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=4), encoding="utf-8")
        log(f"已生成配置模板：{CONFIG_PATH}，填好 server_id 和 token 后重新运行")
        sys.exit(1)
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    if "在这里填" in cfg["server_id"] or "在这里填" in cfg["token"]:
        log(f"请先填写 {CONFIG_PATH} 里的 server_id 和 token")
        sys.exit(1)
    return cfg


def api_state(cfg):
    req = urllib.request.Request(
        f"https://voer.host/api/servers/{cfg['server_id']}",
        headers={"Cookie": f"token={cfg['token']}", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["server"]


def click_anywhere(page, texts, timeout_ms):
    """在所有 frame（含跨进程 iframe）里找文本并真实点击"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for frame in page.frames:
            for t in texts:
                for maker in (lambda: frame.get_by_text(t, exact=True).first,
                              lambda: frame.get_by_role("button", name=t).first):
                    try:
                        loc = maker()
                        if loc.count() and loc.is_visible():
                            loc.click(timeout=3000)
                            return f"{t}@{frame.url[:45]}"
                    except Exception:
                        pass
        time.sleep(1.5)
    return None


def main():
    cfg = load_config()
    server_id = cfg["server_id"]
    url = f"https://voer.host/panel/server/{server_id}"

    if "--status" in sys.argv:
        s = api_state(cfg)
        for k in ("status", "sessionExpiresAt", "sessionExtensions",
                  "sessionExtensionsToday", "sessionDuration", "adsWatched"):
            print(f"{k} = {s.get(k)}")
        return

    with sync_playwright() as p:
        launch = dict(headless=cfg["headless"],
                      args=["--disable-blink-features=AutomationControlled",
                            "--window-size=1400,1000"])
        if cfg["use_system_chrome"]:
            launch["channel"] = "chrome"
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        ctx.add_cookies([{"name": "token", "value": cfg["token"],
                          "domain": "voer.host", "path": "/", "secure": True}])
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(7000)

        before = api_state(cfg)
        log("当前到期:", before.get("sessionExpiresAt"),
            "| 已续期:", before.get("sessionExtensions"),
            "| 今日:", before.get("sessionExtensionsToday"))

        try:
            page.get_by_role("button", name="Accept").first.click(timeout=3000)
        except Exception:
            pass
        page.get_by_role("button", name="延伸", exact=True).first.click()
        page.wait_for_timeout(2500)
        page.get_by_role("button", name="觀看廣告", exact=True).first.click()
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
            log(f"第 {i} 个广告:", f"已关闭（{closed}）" if closed else "未找到 Close（可能自动关闭）")
            page.wait_for_timeout(6000)

        end = time.time() + 180
        while time.time() < end:
            try:
                now = api_state(cfg)
            except Exception:
                now = None
            if now and (now.get("sessionExtensions", 0) > before.get("sessionExtensions", 0)
                        or now.get("sessionExpiresAt") != before.get("sessionExpiresAt")):
                log("续期成功 -> 新到期:", now.get("sessionExpiresAt"),
                    "| 累计:", now.get("sessionExtensions"),
                    "| 今日:", now.get("sessionExtensionsToday"))
                break
            time.sleep(10)
        else:
            log("未检测到续期生效，请检查窗口是否卡在某个广告上")

        page.wait_for_timeout(3000)
        browser.close()


if __name__ == "__main__":
    main()
