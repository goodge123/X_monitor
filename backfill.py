#!/usr/bin/env python3
"""补推断档期间的推文。用法：
    sudo .venv/bin/python backfill.py "2026-10-19 07:00" "2026-10-19 15:45"
只补 [项目] 的（AI_FILTER_IDLE=true 时），带纠正按钮。
"""
import sys
from datetime import datetime
import feedparser
import requests

from config import load_rss_config
import rss_monitor
from notifier import send_to_telegram


def parse_time(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def main():
    if len(sys.argv) != 3:
        print(f"用法: {sys.argv[0]} 'YYYY-MM-DD HH:MM' 'YYYY-MM-DD HH:MM'")
        sys.exit(1)
    start = parse_time(sys.argv[1])
    end = parse_time(sys.argv[2])
    print(f"补推窗口: {start} ~ {end}")

    cfg = load_rss_config()
    pushed = 0
    skipped_idle = 0
    for feed_url in cfg.rss_feeds:
        # 兼容 username|url 格式
        if "|" in feed_url:
            display_name, url = feed_url.split("|", 1)
        else:
            url = feed_url
            display_name = url.split("/twitter/user/")[-1] if "/twitter/user/" in url else url
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as exc:
            print(f"[{display_name}] 抓取失败: {exc}")
            continue
        for entry in feed.entries:
            # 取发布时间
            pub = entry.get("published_parsed") or entry.get("updated_parsed")
            if not pub:
                continue
            pub_dt = datetime(*pub[:6])
            if not (start <= pub_dt <= end):
                continue
            summary = rss_monitor.clean_text(entry.get("summary") or entry.get("title", ""))
            tag = rss_monitor.classify_tweet(cfg, summary)
            if tag == "[闲聊]" and cfg.ai_filter_idle:
                skipped_idle += 1
                continue
            if not tag:
                # AI 失败的也推（带不上标签）
                pass
            msg = rss_monitor.build_message(cfg, display_name, entry, tag)
            # 纠正按钮（有标签才加）
            reply_markup = None
            if tag:
                eid = rss_monitor.entry_id(entry)
                if eid:
                    reply_markup = rss_monitor.build_label_keyboard(eid)
            ok = send_to_telegram(cfg, msg, reply_markup=reply_markup)
            print(f"[{display_name}] {pub_dt} {tag or '[无标签]'} -> {'OK' if ok else 'FAIL'}")
            if ok:
                pushed += 1
    print(f"完成: 推送 {pushed} 条, 跳过闲聊 {skipped_idle} 条")


if __name__ == "__main__":
    main()
