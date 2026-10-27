"""通过 RSS 监听 X(Twitter) 用户，将新条目推送到 Telegram。

这是不依赖付费 X API 的替代方案：借助第三方 RSS 源
（Nitter / RSSHub / rss.app 等），轮询 feed 并推送新条目。

配置见 config.py 的 load_rss_config()：
- RSS_FEEDS：直接给出完整 feed URL（逗号分隔，可用 `名称|URL`）
- 或 RSS_BASE_URL + USERNAMES_TO_TRACK：按模板为每个用户拼接 URL
"""
from __future__ import annotations

import html
import logging
import re
import signal
import sys
from typing import Any

import feedparser
import requests

import notifier
from config import ConfigError, RSSConfig, load_rss_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("x_monitor")

HTTP_TIMEOUT = 120  # 秒（自建 RSSHub 冷缓存回源可能较慢，放宽避免误判失败）
MAX_NEW_PER_POLL = 5  # 单个 feed 每轮最多推送数，避免首次或积压时刷屏
# 部分实例（如 xcancel）会检查请求头，仅对“RSS 客户端”返回内容，
# 浏览器 UA 反而被拒（400: This URL only works inside an RSS client）。
# 因此使用 RSS 阅读器风格的 UA，并声明接受 RSS/XML。
USER_AGENT = "FreshRSS/1.24 (Linux; https://freshrss.org)"
FEED_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}


def _handle_signal(signum: int, _frame: Any) -> None:
    logger.info("收到信号 %s，正在安全退出……", signum)
    notifier.request_shutdown()


def fetch_feed(url: str) -> feedparser.FeedParserDict | None:
    """抓取并解析单个 feed，失败返回 None。"""
    try:
        resp = requests.get(url, headers=FEED_HEADERS, timeout=HTTP_TIMEOUT)
    except requests.RequestException as exc:
        logger.error("抓取 RSS 失败 %s：%s", url, exc)
        return None

    if resp.status_code != 200:
        # 附带响应体片段，便于定位 400/403 等的具体原因
        body = resp.text[:200].replace("\n", " ").strip()
        logger.error("抓取 RSS 返回 %s：%s ｜ 响应：%s", resp.status_code, url, body)
        return None

    # 部分实例（如 xcancel）返回的 XML 带前导空白/BOM，先去掉再解析
    content = resp.content.lstrip()
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        logger.error("解析 RSS 失败 %s：%s", url, parsed.bozo_exception)
        return None
    return parsed


def fetch_first_available(urls: tuple[str, ...]) -> feedparser.FeedParserDict | None:
    """依次尝试候选镜像，返回第一个成功且含条目的 feed。"""
    for url in urls:
        parsed = fetch_feed(url)
        if parsed and parsed.entries:
            if len(urls) > 1:
                logger.info("使用镜像：%s", url)
            return parsed
    return None


def entry_id(entry: feedparser.FeedParserDict) -> str:
    """取条目的稳定唯一标识。"""
    return str(entry.get("id") or entry.get("link") or entry.get("title", ""))


def clean_text(raw: str) -> str:
    """去掉 HTML 标签与转义，得到纯文本。"""
    import re

    text = re.sub(r"<[^>]+>", "", raw or "")
    return html.unescape(text).strip()


def _ai_chat(cfg: RSSConfig, system: str, user: str, max_tokens: int = 10) -> str:
    """调用 OpenAI 兼容接口，返回助手回复文本。失败返回空字符串。"""
    try:
        proxy_url = cfg.ai_proxy.strip()
        # 兼容 host:port:user:pass 格式，转成 http://user:pass@host:port
        if proxy_url and "://" not in proxy_url:
            parts = proxy_url.split(":")
            if len(parts) == 4:
                h, p, u, pw = parts
                proxy_url = f"http://{u}:{pw}@{h}:{p}"
            elif len(parts) == 2:
                proxy_url = f"http://{proxy_url}"
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        resp = requests.post(
            cfg.ai_base_url.rstrip("/") + "/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg.ai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg.ai_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user[:800]},
                ],
                "max_tokens": max_tokens,
                "temperature": 0,
            },
            timeout=30,
            proxies=proxies,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # AI 失败不阻塞推送
        logger.warning("AI 调用失败：%s", exc)
    return ""


def classify_tweet(cfg: RSSConfig, text: str) -> str:
    """用 AI 判断推文类型，返回 [项目] 或 [闲聊]。未配置 AI 则返回空字符串。"""
    if not cfg.ai_api_key or not cfg.ai_base_url:
        return ""
    # 太短的纯转发/表情直接算闲聊，不浪费 API
    if len(text.strip()) < 10:
        return "[闲聊]"
    label = _ai_chat(
        cfg,
        "你是一个加密货币推文分类器。判断推文是「项目相关」还是「闲聊」。"
        "项目相关：在讨论具体代币/项目、合约地址、空投、IDO、价格走势、技术分析、项目动态。"
        "闲聊：日常生活、情绪抒发、无实质信息的灌水、纯表情。"
        "只回答「项目」或「闲聊」两个字，不要解释。",
        text,
    )
    if "项目" in label:
        return "[项目]"
    if "闲聊" in label:
        return "[闲聊]"
    return ""


_URL_RE = re.compile(r"https?://\S+")
_QUOTE_BLOCK_RE = re.compile(r'<div class="rsshub-quote">.*?</div>', re.S)


def _lang_counts(text: str) -> tuple[int, int]:
    """统计文本（先剥 URL）的 CJK 表意字数与拉丁字母数。"""
    plain = _URL_RE.sub(" ", text or "")
    cjk = latin = 0
    for ch in plain:
        o = ord(ch)
        if (
            (0x4E00 <= o <= 0x9FFF)
            or (0x3400 <= o <= 0x4DBF)
            or (0x3040 <= o <= 0x30FF)
            or (0xAC00 <= o <= 0xD7AF)
        ):
            cjk += 1
        elif ch.isascii() and ch.isalpha():
            latin += 1
    return cjk, latin


def is_chinese_dominant(text: str) -> bool:
    """中文占主导：CJK 不少于 5 字，且 CJK / (CJK + 拉丁字母) >= 0.5。"""
    cjk, latin = _lang_counts(text)
    if cjk < 5:
        return False
    if latin == 0:
        return True
    return cjk / (cjk + latin) >= 0.5


def tweet_own_text(raw: str) -> str:
    """从条目 summary 的原始 HTML 提取推文自身文本，供语言判断：
    去掉 RSSHub 追加的链接卡片块（rsshub-quote）、所有标签与 URL，
    避免链接预览里的英文站描污染语言构成统计。"""
    t = _QUOTE_BLOCK_RE.sub(" ", raw or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    return _URL_RE.sub(" ", t).strip()


def translate_tweet(cfg: RSSConfig, text: str, own_text: str | None = None) -> str:
    """英文推文翻成中文。中文占主导或未配置 AI 返回空字符串（不调用翻译接口）。"""
    if not cfg.ai_api_key or not cfg.ai_base_url:
        return ""
    # 语言判断只看推文自身文本（已剥链接卡片块与 URL）：中文主导直接跳过
    probe = own_text if own_text is not None else text
    if is_chinese_dominant(probe):
        return ""
    if len(text.strip()) < 20:
        return ""
    _, latin = _lang_counts(probe)
    if latin == 0:
        return ""
    trans = _ai_chat(
        cfg,
        "你是翻译助手。把用户发来的英文推文翻译成简洁的中文。"
        "如果已经是中文或无法翻译，只回答「无」。",
        text,
        max_tokens=400,
    )
    if not trans or trans == "无":
        return ""
    return trans


def build_message(cfg: RSSConfig, display_name: str, entry: feedparser.FeedParserDict, tag: str = "") -> str:
    raw_summary = entry.get("summary") or entry.get("title", "")
    summary = clean_text(raw_summary)
    link = entry.get("link", "")
    prefix = f"{tag} " if tag else ""
    parts = [f"{prefix}用户 {display_name} 的最新推文：", "", f"原文：{summary}"]
    # 英文推文自动翻译（是否翻译按推文自身文本的语言构成判断）
    trans = translate_tweet(cfg, summary, own_text=tweet_own_text(raw_summary))
    if trans:
        parts += ["", f"翻译：{trans}"]
    if link:
        parts += ["", f"链接：{link}"]
    return "\n".join(parts)


def build_label_keyboard(entry_id_str: str) -> dict:
    """推文下方的 [项目] / [闲聊] 纠正按钮。callback_data 限 64 字节，用短 hash。"""
    import hashlib

    short_id = hashlib.md5(entry_id_str.encode()).hexdigest()[:16]
    return {
        "inline_keyboard": [
            [
                {"text": "项目", "callback_data": f"lp:{short_id}"},
                {"text": "闲聊", "callback_data": f"li:{short_id}"},
            ]
        ]
    }


def poll_feed(
    cfg: RSSConfig,
    display_name: str,
    state_key: str,
    urls: tuple[str, ...],
    state: dict[str, str],
) -> None:
    parsed = fetch_first_available(urls)
    if not parsed:
        return

    entries = list(parsed.entries)
    if not entries:
        return

    last_seen = state.get(state_key)

    # feedparser 通常按从新到旧排列；收集 last_seen 之前的新条目
    new_entries = []
    for entry in entries:
        eid = entry_id(entry)
        if eid == last_seen:
            break
        new_entries.append(entry)

    if last_seen is None:
        # 首次运行：只记录最新条目，不回推历史，避免刷屏
        state[state_key] = entry_id(entries[0])
        notifier.save_state(cfg.state_file, state)
        logger.info("初始化 %s，记录最新条目为基线。", display_name)
        return

    if not new_entries:
        return

    # 从旧到新推送，并限制单轮数量
    for entry in reversed(new_entries[:MAX_NEW_PER_POLL]):
        eid = entry_id(entry)
        summary = clean_text(entry.get("summary") or entry.get("title", ""))
        tag = classify_tweet(cfg, summary)
        # AI 开启时只推项目相关，闲聊直接跳过（仍更新基线避免重复）
        if tag == "[闲聊]" and cfg.ai_filter_idle:
            logger.info("跳过闲聊 %s：%s", display_name, eid)
            state[state_key] = eid
            notifier.save_state(cfg.state_file, state)
            continue
        logger.info("新条目 %s：%s", display_name, eid)
        message = build_message(cfg, display_name, entry, tag)
        # AI 分类启用时带上纠正按钮
        keyboard = build_label_keyboard(eid) if tag else None
        notifier.send_to_telegram(
            cfg.telegram_bot_token, cfg.telegram_chat_id, message, cfg.pin_message,
            reply_markup=keyboard,
        )
        # QQ 双推（已配置才发；失败只记日志，不影响 TG 与基线更新）
        if cfg.qq_onebot_token and cfg.qq_group_id:
            notifier.send_to_qq_group(
                cfg.qq_onebot_url, cfg.qq_onebot_token, cfg.qq_group_id, message
            )
        # QQ 官方机器人推送（配齐才发；失败只记日志）
        if cfg.qq_official_appid and cfg.qq_official_secret and cfg.qq_official_group_openid:
            notifier.send_to_qq_official(
                cfg.qq_official_appid,
                cfg.qq_official_secret,
                cfg.qq_official_group_openid,
                message,
            )
        state[state_key] = eid
        notifier.save_state(cfg.state_file, state)

    if len(new_entries) > MAX_NEW_PER_POLL:
        logger.warning(
            "%s 本轮有 %d 条新内容，仅推送最新 %d 条。",
            display_name,
            len(new_entries),
            MAX_NEW_PER_POLL,
        )
        # 将基线更新到最新，避免下轮重复处理被跳过的旧条目
        state[state_key] = entry_id(entries[0])
        notifier.save_state(cfg.state_file, state)


def run() -> int:
    try:
        cfg = load_rss_config()
    except ConfigError as exc:
        logger.error("配置错误：%s", exc)
        return 1

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    for name, _key, urls in cfg.feeds:
        logger.info("监听 RSS：%s -> %s", name, "，".join(urls))

    state = notifier.load_state(cfg.state_file)
    logger.info("开始监听（间隔 %d 秒）。按 Ctrl+C 停止。", cfg.poll_interval)

    while not notifier.is_shutting_down():
        for name, key, urls in cfg.feeds:
            if notifier.is_shutting_down():
                break
            poll_feed(cfg, name, key, urls, state)
            notifier.interruptible_sleep(cfg.request_delay)
        notifier.interruptible_sleep(cfg.poll_interval)

    logger.info("已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(run())
