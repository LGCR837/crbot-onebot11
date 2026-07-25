"""
bridge.py - OldChat ↔ OneBot11 协议桥接器
"""

import asyncio
import json
import logging
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
        self._loop = None
        self._msg_id_counter = 0

        self.group_mapping = {k: int(v) for k, v in config.get("group_mapping", {}).items()}
        self.reverse_mapping = {v: k for k, v in self.group_mapping.items()}

    def set_loop(self, loop):
        self._loop = loop

    def _next_msg_id(self):
        self._msg_id_counter += 1
        return self._msg_id_counter

    # ==================== OldChat WS → OneBot11 ====================

    async def oldchat_ws_handler(self, msg: dict):
        msg_type = msg.get("type", "")

        if msg_type == "group_message":
            await self._handle_group_message(msg)
        elif msg_type == "direct_message":
            await self._handle_direct_message(msg)

    async def _handle_group_message(self, msg: dict):
        data = msg.get("data", {})
        group_id = data.get("group_id", "")
        from_uid = data.get("from_uid", "")
        body = data.get("body", "")
        msg_id = data.get("id", "")

        if not group_id or group_id not in self.group_mapping:
            return

        onebot_group_id = self.group_mapping[group_id]

        if isinstance(body, dict):
            body = body.get("text", json.dumps(body, ensure_ascii=False))
        elif isinstance(body, str) and body.startswith("{"):
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    body = parsed.get("text", body)
            except json.JSONDecodeError:
                pass

        if not body:
            return

        logger.info("OldChat → OneBot11: 群 %s, 发送者 %s, 内容: %s", group_id, from_uid, body[:50])

        user_id = int(from_uid) if from_uid and from_uid.isdigit() else hash(from_uid) % (10**9)

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
                "nickname": from_uid,
                "card": from_uid,
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

        if self._loop:
            coro = self.onebot.send_event(event)
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _handle_direct_message(self, msg: dict):
        data = msg.get("data", {})
        from_uid = data.get("from_uid", "")
        body = data.get("body", "")

        if not body:
            return

        logger.info("OldChat → OneBot11: 私聊 %s, 内容: %s", from_uid, body[:50])

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
        pass
