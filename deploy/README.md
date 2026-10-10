# 部署说明（VPS 自建 RSSHub + X 监控）

## 架构

- 5 个 RSSHub 实例（`rsshub-a`~`rsshub-e`），端口 1200–1204
- 每实例 2 个 X 账号 token + 1 个独立代理 IP
- `xmonitor` 轮询各实例的 KOL feed，新推文推送到 Telegram 频道

## 服务器上需要的文件（不进 git，权限 600）

| 文件 | 内容 | 格式 |
|------|------|------|
| `~/x-monitor/.tokens` | 10 个 X 账号的登录 token | `# account N` + `auth_token=` + `ct0=` |
| `~/x-monitor/.proxies10` | 代理列表 | `ip:port:user:pass`，一行一个 |
| `~/x-monitor/X_monitor/.env` | TG bot token、KOL 列表等 | 参考 `.env.example` |

## 部署步骤

1. 装好 Node 22、RSSHub（tsx 模式跑，2GB 机器跳过 `pnpm build`，但必须跑 `build:routes` 生成 `assets/build/routes.js`）
2. `deploy/start-rsshub.sh` → `~/x-monitor/scripts/start-rsshub.sh`
3. `deploy/warm-feeds.sh` → `~/x-monitor/scripts/warm-feeds.sh`
4. 以 root 跑 `deploy/setup-instances.sh` 生成 systemd units
5. 配置 `.env`（`RSS_FEEDS=名称|http://localhost:端口/twitter/user/xxx,...`）
6. 加 cron 预热：`*/4 * * * * root /root/x-monitor/scripts/warm-feeds.sh`
7. `systemctl start rsshub-a rsshub-b rsshub-c rsshub-d rsshub-e xmonitor`

## 注意事项

- 2GB 内存：不要同时重启 5 个实例，会 OOM。逐个重启，间隔 30 秒。
- RSSHub 会自动剔除失效的 token（401/403），用 `deploy/verify-tokens.sh` 可逐个验证。
- 加 KOL 时：改 `.env` 的 `RSS_FEEDS` + `warm-feeds.sh`，重启 xmonitor。
