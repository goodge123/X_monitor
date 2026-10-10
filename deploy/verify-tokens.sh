#!/usr/bin/env bash
# 逐个验证 10 个 X 账号的 token 是否有效。在 VPS 上以 root 运行。
set -u
BASE=/root/x-monitor
TOKENS_FILE="$BASE/.tokens"

# 从 RSSHub 源码提取 bearer token（X 官方 web 端公用 bearer，非用户密钥）
BEARER="$(grep -oP "bearerToken = '\K[^']+" "$BASE/rsshub/lib/routes/twitter/api/web-api/constants.ts" | head -1)"
if [[ -z "$BEARER" ]]; then
  echo "无法提取 bearer token"
  exit 1
fi

# 获取最新的 UserByScreenName queryId（失败则用 fallback）
QID="$(curl -s --max-time 20 "https://cdn.jsdelivr.net/gh/fa0311/TwitterInternalAPIDocument@master/docs/json/API.json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('graphql',{}).get('UserByScreenName',{}).get('queryId',''))" 2>/dev/null)"
[[ -z "$QID" ]] && QID="Gb-d6r0vxPOADdG62OEBpQ"
echo "使用 queryId: $QID"
echo "----------------------------------------"

# 解析 10 个账号的 auth_token 和 ct0
declare -a ACCOUNTS_AUTH
declare -a ACCOUNTS_CT0
cur=0
while IFS= read -r line; do
  if [[ "$line" =~ ^#\ account\ ([0-9]+) ]]; then
    cur="${BASH_REMATCH[1]}"
  elif [[ "$line" =~ ^auth_token=(.+) ]] && (( cur >= 1 )); then
    ACCOUNTS_AUTH[$((cur-1))]="${BASH_REMATCH[1]}"
  elif [[ "$line" =~ ^ct0=(.+) ]] && (( cur >= 1 )); then
    ACCOUNTS_CT0[$((cur-1))]="${BASH_REMATCH[1]}"
  fi
done < "$TOKENS_FILE"

VARS='{"screen_name":"elonmusk","withSafetyModeUserFields":true}'
FEATS='{}'
URL="https://x.com/i/api/graphql/${QID}/UserByScreenName?variables=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$VARS")&features=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$FEATS")"

ok=0; bad=0
for i in 0 1 2 3 4 5 6 7 8 9; do
  n=$((i+1))
  at="${ACCOUNTS_AUTH[$i]:-}"
  ct="${ACCOUNTS_CT0[$i]:-}"
  if [[ -z "$at" ]]; then
    echo "账号 $n: 文件中未找到 token -> SKIP"
    continue
  fi
  code="$(curl -s -o /tmp/tok_$n.json -w "%{http_code}" --max-time 30 \
    -H "authorization: $BEARER" \
    -H "x-csrf-token: $ct" \
    -H "x-twitter-auth-type: OAuth2Session" \
    -H "x-twitter-active-user: yes" \
    -H "x-twitter-client-language: en" \
    -H "referer: https://x.com/" \
    -b "auth_token=$at; ct0=$ct" \
    "$URL")"
  # 200 且返回里有用户数据才算有效
  if [[ "$code" == "200" ]] && grep -q '"screen_name"' /tmp/tok_$n.json 2>/dev/null; then
    echo "账号 $n: OK (200)"
    ok=$((ok+1))
  else
    echo "账号 $n: FAIL (HTTP $code)"
    bad=$((bad+1))
  fi
  sleep 2
done
echo "----------------------------------------"
echo "有效: $ok, 失效: $bad"
