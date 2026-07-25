"""
bridge.py - OldChat ↔ OneBot11 协议桥接器
"""

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from oldchat_client import OldChatClient
from onebot_client import OneBotClient

logger = logging.getLogger("bridge")


class Bridge:
    def __init__(self, oldchat: OldChatClient, onebot: OneBotClient, config: dict):
        self.oldchat = oldchat
        self.onebot = onebot
        self.config = config
        self.processed_ids: set = set()
        self._temp_dir = Path(__file__).parent / "temp"
        self._temp_dir.mkdir(exist_ok=True)
        self._pending_events = []
        self._loop = None
        self._msg_id_counter = 0

        self.group_mapping = {k: int(v) for k, v in config.get("group_mapping", {}).items()}
        self.reverse_mapping = {v: k for k, v in self.group_mapping.items()}
        logger.info("群组映射已加载: %s", self.reverse_mapping)

    def set_loop(self, loop):
        self._loop = loop

    def _next_msg_id(self):
        self._msg_id_counter += 1
        return self._msg_id_counter

    # ==================== OldChat → OneBot11 ====================

    def oldchat_to_onebot_from_thread(self, group_id: str):
        try:
            messages = self.oldchat.get_group_messages(group_id, limit=10, offset=0)
        except Exception as e:
            logger.warning("获取 OldChat 群 %s 消息失败: %s", group_id, e)
            return

        onebot_group_id = self.group_mapping.get(group_id)
        if not onebot_group_id:
            return

        for msg in messages:
            msg_id = msg.get("id") or msg.get("msg_id") or msg.get("_id")
            if not msg_id or msg_id in self.processed_ids:
                continue

            self.processed_ids.add(msg_id)

            body = msg.get("body", "") or ""
            sender_name = msg.get("sender_name") or msg.get("sender_uid") or "用户"
            sender_uid = msg.get("sender_uid", "")

            if isinstance(body, dict):
                body = body.get("text", json.dumps(body, ensure_ascii=False))
            elif isinstance(body, str) and body.startswith("{"):
                try:
                    parsed = json.loads(body)
                    if isinstance(parsed, dict):
                        body = parsed.get("text", body)
                except json.JSONDecodeError:
                    pass

            if not isinstance(body, str):
                continue

            logger.info("OldChat → OneBot11: 群 %s, 发送者 %s, 内容: %s", group_id, sender_name, body[:50])

            user_id = int(sender_uid) if sender_uid and sender_uid.isdigit() else hash(sender_name) % (10**9)

            event = {
                "post_type": "message",
                "message_type": "group",
                "sub_type": "normal",
                "group_id": onebot_group_id,
                "user_id": user_id,
                "message_id": self._next_msg_id(),
                "message": [{"type": "text", "data": {"text": body}}],
                "raw_message": body,
                "font": 0,
                "sender": {
                    "user_id": user_id,
                    "nickname": sender_name,
                    "card": sender_name,
                    "sex": "unknown",
                    "age": 0,
                    "area": "",
                    "level": "",
                    "role": "member",
                    "title": "",
                },
                "time": int(time.time()),
                "self_id": self.onebot.self_id,
            }

            self._pending_events.append(event)

    def _send_pending_from_thread(self):
        if not self._pending_events or not self._loop:
            return

        while self._pending_events:
            event = self._pending_events.pop(0)
            try:
                coro = self.onebot.send_event(event)
                asyncio.run_coroutine_threadsafe(coro, self._loop)
            except Exception as e:
                logger.error("提交事件失败: %s", e)

    # ==================== OneBot11 → OldChat ====================

    async def handle_api_call(self, action: str, **kwargs):
        if action == "send_group_msg":
            onebot_group_id = int(kwargs.get("group_id", 0))
            message = kwargs.get("message", "")

            oldchat_group_id = self.reverse_mapping.get(onebot_group_id)
            if not oldchat_group_id:
                logger.warning("未找到 OneBot11 群 %s 对应的 OldChat 群", onebot_group_id)
                return

            try:
                self.oldchat.send_group_message(oldchat_group_id, message)
                logger.info("消息已转发到 OldChat: 群 %s", oldchat_group_id)
            except Exception as e:
                logger.error("转发到 OldChat 失败: %s", e)
                raise

        elif action == "send_private_msg":
            logger.info("API 调用: send_private_msg (暂不支持)")

        else:
            logger.warning("未知的 API 调用: %s", action)

    async def onebot_to_oldchat(self, msg: dict):
        post_type = msg.get("post_type", "")

        if post_type == "message":
            group_id = msg.get("group_id")
            raw_message = msg.get("raw_message", "") or msg.get("message", "")
            sender = msg.get("sender", {})
            sender_name = sender.get("nickname", "") or sender.get("card", "")

            if not group_id:
                return

            oldchat_group_id = self.reverse_mapping.get(group_id)
            if not oldchat_group_id:
                return

            if isinstance(raw_message, list):
                text_parts = []
                for elem in raw_message:
                    if isinstance(elem, dict) and elem.get("type") == "text":
                        text_parts.append(elem.get("data", {}).get("text", ""))
                text = "".join(text_parts).strip()
            else:
                text = str(raw_message).strip()

            if text:
                try:
                    self.oldchat.send_group_message(oldchat_group_id, text)
                    logger.info("消息已转发到 OldChat: 群 %s", oldchat_group_id)
                except Exception as e:
                    logger.error("转发失败: %s", e)

    # ==================== 维护 ====================

    def cleanup_processed_ids(self, max_size: int = 10000):
        if len(self.processed_ids) > max_size:
            self.processed_ids = set(list(self.processed_ids)[-max_size // 2:])
