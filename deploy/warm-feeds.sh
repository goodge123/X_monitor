#!/usr/bin/env bash
# RSSHub 缓存预热：每 4 分钟跑一次，保持各实例缓存热，避免监控轮询时冷启动超时。
# 配合 /etc/cron.d/xmon-warm 使用。KOL 增减时同步改这里。
set -u
LOCK=/tmp/xmon_warm.lock
exec 9>"$LOCK"
flock -n 9 || exit 0
curl -s -o /dev/null --max-time 200 "http://localhost:1200/twitter/user/sanyi_eth_" || true
curl -s -o /dev/null --max-time 200 "http://localhost:1200/twitter/user/NFTCPS" || true
curl -s -o /dev/null --max-time 200 "http://localhost:1200/twitter/user/GuarEmperor" || true
curl -s -o /dev/null --max-time 200 "http://localhost:1201/twitter/user/Auduoduo" || true
curl -s -o /dev/null --max-time 200 "http://localhost:1201/twitter/user/wsdxbz1" || true
curl -s -o /dev/null --max-time 200 "http://localhost:1202/twitter/user/KuiGas" || true
curl -s -o /dev/null --max-time 200 "http://localhost:1203/twitter/user/wolfyxbt" || true
curl -s -o /dev/null --max-time 200 "http://localhost:1204/twitter/user/luge517" || true
