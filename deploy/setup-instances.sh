#!/usr/bin/env bash
# 生成 rsshub-a~e 5 个 systemd 实例（每实例 2 个号 + 1 个独立代理）。以 root 运行。
set -euo pipefail

names=(a b c d e)
ports=(1200 1201 1202 1203 1204)
tokens=("1-2" "3-4" "5-6" "7-8" "9-10")

for idx in 0 1 2 3 4; do
  n=${names[$idx]}
  cat > /etc/systemd/system/rsshub-$n.service << EOF
[Unit]
Description=RSSHub instance $n (tokens ${tokens[$idx]}, proxy line $((idx+1)))
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/x-monitor/rsshub
Environment=PORT=${ports[$idx]}
Environment=TOKEN_ACCOUNTS=${tokens[$idx]}
Environment=PROXY_LINE=$((idx+1))
ExecStart=/root/x-monitor/scripts/start-rsshub.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
done

systemctl daemon-reload
for n in a b c d e; do systemctl enable rsshub-$n; done
echo "5 个实例 unit 已生成并 enable"
echo "注意：2GB 内存机器不要同时 restart 全部 5 个，会 OOM。逐个重启，间隔 30 秒。"
