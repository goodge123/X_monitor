"""通用工具：Telegram 推送、状态持久化、可中断等待。

被 API 轮询版(main.py)与 RSS 版(rss_monitor.py)共同复用。
"""
from __future__ import annotations

import json
import logging
import os
import time

import requests

logger = logging.getLogger("x_monitor")

TELEGRAM_API = "https://api.telegram.org"
HTTP_TIMEOUT = 15  # 秒

# 由主程序在收到停止信号时置为 True
shutdown_flag = {"stop": False}


def request_shutdown() -> None:
    shutdown_flag["stop"] = True


def is_shutting_down() -> bool:
    return shutdown_flag["stop"]


def interruptible_sleep(seconds: int) -> None:
    """收到停止信号时可提前退出的 sleep。"""
    for _ in range(seconds):
        if shutdown_flag["stop"]:
            return
        time.sleep(1)


# ---------------------------------------------------------------------------
# 状态（最后处理的条目 ID）持久化
# ---------------------------------------------------------------------------
def load_state(state_file: str) -> dict[str, str]:
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
            logger.warning("状态文件格式不正确，将以空状态启动。")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("读取状态文件失败：%s，将以空状态启动。", exc)
    return {}


def save_state(state_file: str, data: dict[str, str]) -> None:
    """通过临时文件原子写入，防止文件损坏。"""
    tmp = f"{state_file}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, state_file)
    except OSError as exc:
        logger.error("保存状态文件失败：%s", exc)


def _redact(text: str, bot_token: str) -> str:
    """将日志中出现的 bot token 打码，避免凭证泄露。"""
    if bot_token and bot_token in text:
        return text.replace(bot_token, "***")
    return text


# ---------------------------------------------------------------------------
# Telegram 推送
# ---------------------------------------------------------------------------
def send_to_telegram(
    bot_token: str,
    chat_id: str,
    message: str,
    pin_message: bool = False,
    reply_markup: dict | None = None,
) -> int | None:
    """发送消息，返回 message_id（失败返回 None）。"""
    send_url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": False,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(send_url, json=payload, timeout=HTTP_TIMEOUT)
    except requests.RequestException as exc:
        logger.error("Telegram 发送错误：%s", _redact(str(exc), bot_token))
        return None

    if resp.status_code != 200:
        logger.error("Telegram 发送失败 %s：%s", resp.status_code, resp.text[:300])
        return None

    message_id = resp.json().get("result", {}).get("message_id")

    if pin_message and message_id:
        _pin_message(bot_token, chat_id, message_id)

    return message_id


# ---------------------------------------------------------------------------
# QQ 推送（NapCat / OneBot 11 HTTP）
# ---------------------------------------------------------------------------
def send_to_qq_group(
    onebot_url: str, token: str, group_id: str, message: str
) -> bool:
    """通过 OneBot 接口发 QQ 群消息。成功返回 True，任何失败只记日志返回 False。"""
    if not onebot_url or not token or not group_id:
        return False
    try:
        gid: int | str = int(group_id)
    except (TypeError, ValueError):
        gid = str(group_id)
    url = onebot_url.rstrip("/") + "/send_group_msg"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(
            url,
            json={"group_id": gid, "message": message},
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error("QQ 发送错误：%s", exc)
        return False
    if resp.status_code != 200:
        logger.error("QQ 发送失败 %s：%s", resp.status_code, resp.text[:300])
        return False
    try:
        data = resp.json()
    except ValueError:
        logger.error("QQ 发送响应不是 JSON：%s", resp.text[:200])
        return False
    if data.get("status") != "ok" or data.get("retcode") not in (0, None):
        logger.error("QQ 发送被拒：%s", str(data)[:300])
        return False
    return True


# ---------------------------------------------------------------------------
# QQ 官方机器人推送（开放平台 API v2）
# ---------------------------------------------------------------------------
_qq_official_token_cache: dict[str, object] = {"token": "", "expires_at": 0.0}


def _qq_official_access_token(appid: str, secret: str) -> str:
    """取官方机器人 access_token（有效期约 7200 秒，进程内缓存）。失败返回空串。"""
    now = time.time()
    if _qq_official_token_cache["token"] and float(_qq_official_token_cache["expires_at"]) > now + 60:
        return str(_qq_official_token_cache["token"])
    try:
        resp = requests.post(
            "https://bots.qq.com/app/getAppAccessToken",
            json={"appId": appid, "clientSecret": secret},
            timeout=HTTP_TIMEOUT,
        )
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("QQ 官方 token 获取失败：%s", exc)
        return ""
    token = data.get("access_token")
    if not token:
        logger.error("QQ 官方 token 响应异常：%s", str(data)[:200])
        return ""
    try:
        expires_in = int(data.get("expires_in", 7200))
    except (TypeError, ValueError):
        expires_in = 7200
    _qq_official_token_cache["token"] = token
    _qq_official_token_cache["expires_at"] = now + expires_in
    return token


def _qq_official_post(group_openid: str, token: str, payload: dict) -> bool:
    """向官方 API 发一条群消息（payload 已组装好）。成功返回 True。"""
    try:
        resp = requests.post(
            f"https://api.sgroup.qq.com/v2/groups/{group_openid}/messages",
            headers={
                "Authorization": f"QQBot {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error("QQ 官方发送错误：%s", exc)
        return False
    if resp.status_code != 200:
        logger.error("QQ 官方发送失败 %s：%s", resp.status_code, resp.text[:300])
        # token 可能被服务端提前失效，清缓存让下次重取
        _qq_official_token_cache["token"] = ""
        _qq_official_token_cache["expires_at"] = 0.0
        return False
    return True


def send_to_qq_official(
    appid: str,
    secret: str,
    group_openid: str,
    message: str,
    at_everyone: bool = False,
) -> bool:
    """通过官方 API 发群主动消息。成功返回 True，任何失败只记日志返回 False。

    at_everyone=True 时先尝试带 <qqbot-at-everyone /> 的 markdown 消息；
    平台实测该标记能被识别，但 @全体成员需要机器人在群内有管理员权限，
    无权限时接口返回 400——此时降级为普通文本重发，保证消息不丢失，
    待机器人获得管理员权限后 @全体成员即自动生效。
    """
    if not appid or not secret or not group_openid:
        return False
    token = _qq_official_access_token(appid, secret)
    if not token:
        return False
    if at_everyone:
        payload = {
            "msg_type": 2,
            "markdown": {"content": "<qqbot-at-everyone />\n" + message},
        }
        if _qq_official_post(group_openid, token, payload):
            return True
        logger.warning(
            "QQ 官方 @全体成员 发送未成功，降级为普通文本重发（机器人可能缺管理员权限）。"
        )
        # 降级重发前重取 token（失败路径已清缓存）
        token = _qq_official_access_token(appid, secret)
        if not token:
            return False
    return _qq_official_post(group_openid, token, {"content": message, "msg_type": 0})


def _pin_message(bot_token: str, chat_id: str, message_id: int) -> None:
    pin_url = f"{TELEGRAM_API}/bot{bot_token}/pinChatMessage"
    payload = {"chat_id": chat_id, "message_id": message_id}
    try:
        resp = requests.post(pin_url, json=payload, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            logger.warning("置顶消息失败：%s", resp.text[:200])
    except requests.RequestException as exc:
        logger.warning("置顶通信错误：%s", _redact(str(exc), bot_token))
