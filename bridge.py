"""
bridge.py - OldChat ↔ OneBot11 协议桥接器
"""

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

from oldchat_client import OldChatClient
from onebot_client import OneBotClient

logger = logging.getLogger("bridge")

# UID ↔ QQ 映射缓存
_uid_to_qq_cache: dict = {}
_qq_to_uid_cache: dict = {}


def uid_to_qq(uid: str) -> int:
    if uid not in _uid_to_qq_cache:
        qq = abs(hash(uid)) % (10**9) + 100000000
        _uid_to_qq_cache[uid] = qq
        _qq_to_uid_cache[qq] = uid
    return _uid_to_qq_cache[uid]


def qq_to_uid(qq: int) -> Optional[str]:
    return _qq_to_uid_cache.get(qq)


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

    def _uid_to_qq(self, uid: str) -> int:
        return abs(hash(uid)) % (10**9) + 100000000

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
                if isinstance(parsed, dict) and parsed.get("v") == 2:
                    text = parsed.get("text", "")
                    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
                    mentions = parsed.get("mentions", [])
                    for m in mentions:
                        uid = m.get("uid", "")
                        name = m.get("name", uid)
                        qq_num = self._uid_to_qq(uid)
                        text = text.replace(f"@{name}", f"[CQ:at,qq={qq_num}]")
                    quote = parsed.get("quote")
                    if quote and quote.get("id"):
                        text = f"[CQ:reply,id={quote['id']}]{text}"
                    body = text
                elif isinstance(parsed, dict):
                    body = parsed.get("text", body)
            except json.JSONDecodeError:
                pass

        if not body:
            return

        sender_name = from_uid
        try:
            members = self.oldchat.get_group_members(group_id)
            for m in members:
                if m.get("uid") == from_uid:
                    sender_name = m.get("display_name", "") or m.get("username", from_uid)
                    break
        except Exception:
            pass

        logger.info("OldChat → OneBot11: 群 %s, 发送者 %s, 内容: %s", group_id, sender_name, body[:50])

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
                "nickname": sender_name,
                "card": sender_name,
                "sex": "unknown",
                "age": 0,
                "area": "",
                "level": "",
                "role": "member",
                "title": "",
                "avatar": data.get("from_avatar", ""),
            },
            "time": data.get("created_at", int(time.time())),
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

            text_parts = []
            base64_images = []

            if isinstance(message, list):
                for elem in message:
                    if isinstance(elem, dict):
                        elem_type = elem.get("type", "")
                        data = elem.get("data", {})
                        if elem_type == "text":
                            text_parts.append(data.get("text", ""))
                        elif elem_type == "image":
                            file_data = data.get("file", "")
                            if file_data.startswith("base64://"):
                                b64_str = file_data.split(",", 1)[-1] if "," in file_data else file_data.replace("base64://", "")
                                base64_images.append(b64_str)
                message = "".join(text_parts)
            elif not isinstance(message, str):
                message = str(message)

            logger.info("转发文本到 OldChat: 群 %s, 内容: %s", oldchat_group_id, message[:100] if message else "(空)")

            if message:
                try:
                    self.oldchat.send_group_message(oldchat_group_id, message)
                    logger.info("文本消息已转发到 OldChat: 群 %s", oldchat_group_id)
                except Exception as e:
                    logger.error("转发文本失败: %s", e)

            logger.info("解析到 %d 张 Base64 图片", len(base64_images))

            for b64_data in base64_images:
                try:
                    import base64
                    img_bytes = base64.b64decode(b64_data)
                    logger.info("Base64 图片解码成功，大小: %d bytes", len(img_bytes))
                    media_url, thumb_url = self.oldchat.upload_media_bytes(img_bytes, "image.jpg")
                    if media_url:
                        self.oldchat.send_group_message(oldchat_group_id, "", "image",
                                                        media_url=media_url, thumb_url=thumb_url)
                        logger.info("图片已转发到 OldChat: 群 %s, URL: %s", oldchat_group_id, media_url[:50])
                    else:
                        logger.error("图片上传失败，未获得 URL")
                except Exception as e:
                    logger.error("转发图片失败: %s", e)

        elif action == "get_group_member_list":
            onebot_group_id = int(kwargs.get("group_id", 0))
            oldchat_group_id = self.reverse_mapping.get(onebot_group_id)
            if not oldchat_group_id:
                return []

            try:
                members = self.oldchat.get_group_members(oldchat_group_id)
                base_url = self.oldchat.base_url
                result = []
                for m in members:
                    uid = m.get("uid", "")
                    qq_num = self._uid_to_qq(uid)
                    avatar = m.get("avatar_url", "")
                    if avatar and avatar.startswith("/"):
                        avatar = base_url + avatar
                    result.append({
                        "group_id": onebot_group_id,
                        "user_id": qq_num,
                        "nickname": m.get("display_name", "") or m.get("username", uid),
                        "card": m.get("display_name", "") or m.get("username", uid),
                        "sex": "unknown",
                        "age": 0,
                        "join_time": m.get("joined_at", 0),
                        "last_sent_time": 0,
                        "level": "1",
                        "role": "owner" if m.get("role") == 2 else "member",
                        "title": m.get("user_title", ""),
                        "avatar": avatar,
                    })
                return result
            except Exception as e:
                logger.error("获取群成员列表失败: %s", e)
                return []

        elif action == "get_group_info":
            onebot_group_id = int(kwargs.get("group_id", 0))
            oldchat_group_id = self.reverse_mapping.get(onebot_group_id)
            if not oldchat_group_id:
                return {}
            return {
                "group_id": onebot_group_id,
                "group_name": oldchat_group_id,
                "member_count": 0,
            }

        elif action == "send_private_msg":
            logger.info("API 调用: send_private_msg (暂不支持)")
        else:
            logger.warning("未知的 API 调用: %s", action)

    async def onebot_to_oldchat(self, msg: dict):
        pass
