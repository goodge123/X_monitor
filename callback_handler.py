"""Telegram 内联按钮回调处理：接收 [项目]/[闲聊] 纠正点击并记录。

与 rss_monitor.py 独立运行（systemd 服务 xmonitor-callback）。
通过 getUpdates 长轮询监听，rss_monitor 只发送不接收，无冲突。

纠正记录追加到 label_corrections.jsonl，可用于后续优化分类。
"""
from __future__ import annotations

import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import load_rss_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("x_monitor_callback")

TELEGRAM_API = "https://api.telegram.org"
HTTP_TIMEOUT = 30
CORRECTIONS_FILE = Path(__file__).resolve().parent / "label_corrections.jsonl"

_stop = False


def _handle_signal(signum, _frame):
    global _stop
    logger.info("收到信号 %s，正在退出……", signum)
    _stop = True


def answer_callback(bot_token: str, callback_id: str, text: str = "") -> None:
    try:
        requests.post(
            f"{TELEGRAM_API}/bot{bot_token}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("answerCallbackQuery 失败：%s", exc)


def record_correction(entry_id: str, label: str, user: str) -> None:
    """把纠正追加到 jsonl 文件。"""
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "entry_id": entry_id,
        "corrected_label": label,  # project / idle
        "user": user,
    }
    try:
        with open(CORRECTIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.error("写入纠正记录失败：%s", exc)


def run() -> int:
    cfg = load_rss_config()
    bot_token = cfg.telegram_bot_token

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    offset = 0
    logger.info("按钮回调监听已启动。")
    while not _stop:
        try:
            resp = requests.get(
                f"{TELEGRAM_API}/bot{bot_token}/getUpdates",
                params={"offset": offset, "timeout": 25},
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning("getUpdates 失败：%s", exc)
            time.sleep(5)
            continue

        if resp.status_code != 200:
            logger.warning("getUpdates 返回 %s", resp.status_code)
            time.sleep(5)
            continue

        for update in resp.json().get("result", []):
            offset = update["update_id"] + 1
            cq = update.get("callback_query")
            if not cq:
                continue
            data = cq.get("data", "")
            if not data.startswith("label:"):
                continue
            _, label, entry_id = data.split(":", 2)
            user = cq.get("from", {}).get("username", "?")
            label_cn = "项目" if label == "project" else "闲聊"
            record_correction(entry_id, label, user)
            answer_callback(bot_token, cq["id"], f"已标记为「{label_cn}」")
            logger.info("收到纠正 @%s -> %s (%s)", user, label_cn, entry_id[:40])

    logger.info("已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(run())
