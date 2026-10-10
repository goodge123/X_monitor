#!/usr/bin/env bash
# RSSHub 多实例启动脚本。从 ~/.tokens 按 TOKEN_ACCOUNTS 切片取号，
# 可选从 ~/.proxies10 按 PROXY_LINE 取代理。密钥都在 600 权限文件中，不在脚本里。
set -euo pipefail
BASE="$HOME/x-monitor"
cd "$BASE/rsshub"

ACCOUNTS="${TOKEN_ACCOUNTS:-1-10}"
TWITTER_AUTH_TOKEN="$(TOKEN_ACCOUNTS="$ACCOUNTS" TOKENS_FILE="$BASE/.tokens" python3 - <<'PYEOF'
import os, re
want = set()
for part in os.environ["TOKEN_ACCOUNTS"].split(","):
    part = part.strip()
    if not part:
        continue
    if "-" in part:
        a, b = part.split("-", 1)
        want.update(range(int(a), int(b) + 1))
    else:
        want.add(int(part))
toks = {}
cur = None
with open(os.environ["TOKENS_FILE"]) as f:
    for line in f:
        m = re.match(r"# account (\d+)", line)
        if m:
            cur = int(m.group(1))
            continue
        m = re.match(r"auth_token=(\S+)", line)
        if m and cur in want:
            toks[cur] = m.group(1)
print(",".join(toks[i] for i in sorted(toks)))
PYEOF
)"
export TWITTER_AUTH_TOKEN
echo "loaded $(echo "$TWITTER_AUTH_TOKEN" | tr ',' '\n' | grep -c .) twitter auth tokens (accounts $ACCOUNTS)" >&2

# --- 代理：PROXY_LINE 指定 .proxies10 第几行（1-based，格式 ip:port:user:pass），不设则直连 ---
if [[ -n "${PROXY_LINE:-}" && -f "$BASE/.proxies10" ]]; then
    line="$(sed -n "${PROXY_LINE}p" "$BASE/.proxies10")"
    ip="$(echo "$line" | cut -d: -f1)"
    port="$(echo "$line" | cut -d: -f2)"
    puser="$(echo "$line" | cut -d: -f3)"
    ppass="$(echo "$line" | cut -d: -f4)"
    if [[ -n "$ip" && -n "$port" ]]; then
        if [[ -n "$puser" ]]; then
            export PROXY_URI="http://$puser:$ppass@$ip:$port"
        else
            export PROXY_URI="http://$ip:$port"
        fi
        echo "using proxy line $PROXY_LINE ($ip:$port)" >&2
        if ! timeout 20 curl -s -o /dev/null -x "$PROXY_URI" "https://api.ipify.org" ; then
            echo "WARNING: proxy $ip:$port unreachable, falling back to direct" >&2
            unset PROXY_URI
        fi
    fi
fi

export PORT="${PORT:-1200}"
export NODE_ENV=production
export CACHE_EXPIRE="${CACHE_EXPIRE:-300}"

if [[ -f "lib/index.js" ]]; then
    exec node lib/index.js
else
    exec ./node_modules/.bin/tsx lib/index.ts
fi
