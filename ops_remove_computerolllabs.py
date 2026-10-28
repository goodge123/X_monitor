"""一次性运维脚本：把 ComputeRollLabs 从 KOL 监控中移除。

做四件事（在旧机 VPS 上以 root 运行）：
1. 备份 .env；
2. 从 RSS_FEEDS 删除 ComputeRollLabs 条目、从 SPECIAL_HANDLES 删除该账号
   （删空则整行去掉）；
3. 删除 latest_rss_ids.json 中该账号的基线键（状态键就是 feed URL）；
4. 重启 xmonitor 并验活（is-active + 启动日志里监听条数与该账号残留检查）。
执行完打印汇总，供人工核对。
"""
import datetime
import json
import shutil
import subprocess
import time

ENV = "/root/x-monitor/X_monitor/.env"
STATE = "/root/x-monitor/X_monitor/latest_rss_ids.json"
HANDLE = "computerolllabs"


def split_assignment(lines, i, key):
    """取 lines[i] 处 key= 的完整值（处理带引号跨行），返回 (值, 结束行号)。"""
    val = lines[i][len(key) + 1:]
    j = i
    while val.count('"') % 2 == 1 and j + 1 < len(lines):
        j += 1
        val += "\n" + lines[j]
    return val, j


def unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[0], v[1:-1]
    return "", v


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    backup = ENV + ".bak-" + ts
    shutil.copy(ENV, backup)
    print("backup:", backup)

    raw = open(ENV, encoding="utf-8").read()
    lines = raw.split("\n")
    out = []
    i = 0
    feeds_before = feeds_after = -1
    special_before = special_after = None
    while i < len(lines):
        line = lines[i]
        if line.startswith("RSS_FEEDS="):
            val, j = split_assignment(lines, i, "RSS_FEEDS")
            q, inner = unquote(val)
            items = [x.strip() for x in inner.split(",") if x.strip()]
            feeds_before = len(items)
            kept = [x for x in items if HANDLE not in x.lower()]
            feeds_after = len(kept)
            out.append("RSS_FEEDS=" + q + ",".join(kept) + q)
            i = j + 1
            continue
        if line.startswith("SPECIAL_HANDLES="):
            val, j = split_assignment(lines, i, "SPECIAL_HANDLES")
            q, inner = unquote(val)
            names = [x.strip() for x in inner.split(",") if x.strip()]
            special_before = names
            kept = [x for x in names if x.lower() != HANDLE]
            special_after = kept
            if kept:
                out.append("SPECIAL_HANDLES=" + q + ",".join(kept) + q)
            i = j + 1
            continue
        out.append(line)
        i += 1

    open(ENV, "w", encoding="utf-8").write("\n".join(out))
    print("RSS_FEEDS entries: %s -> %s" % (feeds_before, feeds_after))
    print("SPECIAL_HANDLES: %s -> %s" % (special_before, special_after))

    txt = open(ENV, encoding="utf-8").read()
    print("handle left in .env:", HANDLE in txt.lower())

    try:
        st = json.load(open(STATE, encoding="utf-8"))
        keys = [k for k in list(st.keys()) if HANDLE in str(k).lower()]
        for k in keys:
            del st[k]
        json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("state keys removed:", keys)
    except Exception as e:
        print("state cleanup skipped:", e)

    r = subprocess.run(["systemctl", "restart", "xmonitor"])
    print("restart rc:", r.returncode)
    time.sleep(5)
    r = subprocess.run(["systemctl", "is-active", "xmonitor"], capture_output=True, text=True)
    print("xmonitor is-active:", r.stdout.strip())
    r = subprocess.run(
        ["journalctl", "-u", "xmonitor", "--no-pager", "-n", "120"],
        capture_output=True, text=True,
    )
    listen = [ln for ln in r.stdout.split("\n") if "监听 RSS" in ln]
    print("feeds in latest startup log:", len(listen))
    print("handle in latest startup log:", HANDLE in r.stdout.lower())


if __name__ == "__main__":
    main()
