# Voer.host 会话续期（VPS 版）

免费档服务器是会话制（默认 4 小时），续期必须看完 3 个 Google 激励广告，每次 +4 小时。
限制：每 UTC 日最多 4 次、每会话最多 4 次。

## 1. 安装

```bash
pip install -r requirements.txt
playwright install chromium          # 下载浏览器（约 150MB）
sudo apt install -y xvfb             # VPS 无桌面时必须装，否则广告不会发奖励
```

若要改用系统 Chrome：`use_system_chrome: true`，并先 `sudo apt install google-chrome-stable`。

## 2. 配置

复制模板并填写（**token 与 server_id 不入库，只有你本地/VPS 上有**）：

```bash
cp config.example.json config.json
```

| 字段 | 说明 |
| --- | --- |
| `server_id` | 面板地址 `https://voer.host/panel/server/<这段>` |
| `token` | 浏览器 Cookie 中 `voer.host` 的 `token`（JWT，约 7 天有效，过期后重新从浏览器复制） |
| `ads_per_extension` | 每次续期需要的广告数，一般 3 |
| `ad_duration_sec` | 单个广告播放时长，广告变长就调大 |
| `headless` | 必须 `false` |
| `use_system_chrome` | `false` 用 playwright 自带 chromium |

第一次直接跑 `python3 voer_renew.py` 也会自动生成 `config.json` 模板。

## 3. 运行

```bash
xvfb-run -a python3 voer_renew.py           # 续期一次（3 个广告，约 3 分钟）
python3 voer_renew.py --status              # 只看状态，不消耗广告
```

输出示例：

```
[05:43:24] 已点击第 1/3 个 Watch ad（wormies.voer.host）
[05:43:56] 第 1 个广告: 已关闭
[05:45:32] 续期成功 -> 新到期: 2026-09-10T13:00:06Z | 累计: 3 | 今日: 3
```

## 4. 定时续期（可选）

每天在到期前跑一次，例如每天本地时间 8:00、16:00、0:00：

```bash
crontab -e
0 0,8,16 * * * cd /root/voer_renew && /usr/bin/xvfb-run -a /usr/bin/python3 voer_renew.py >> renew.log 2>&1
```

建议先用 `--status` 确认 token 有效再挂 cron。

## 5. 注意事项

- 必须**有虚拟显示**（xvfb）且 `headless: false`；无头模式广告不会发奖励，站点还会检测广告拦截（`bait_timeout` / `cosmetic_filter`）
- 不要开广告拦截插件
- token 过期后表现为 `--status` 报 401/403，重新从浏览器复制 cookie 即可
- 一天最多 4 次，超过后接口会拒绝，脚本会打印"未检测到续期生效"
