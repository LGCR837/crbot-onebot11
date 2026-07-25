"""
onebot_client.py - OneBot11 WebSocket 客户端
直接实现 OneBot V11 协议，连接到 AstrBot 的 aiocqhttp
"""

import asyncio
import json
import logging
import time
from typing import Callable, Awaitable, Optional

import websockets

logger = logging.getLogger("onebot")


class OneBotClient:
    def __init__(self, endpoint: str, self_id: str = "crbot", token: str = None):
        self.endpoint = endpoint
        self.self_id = self_id
        self.token = token
        self._message_handler: Optional[Callable] = None
        self._api_handler: Optional[Callable] = None
        self._ws = None
        self._api_call_id = 0
        self._api_calls = {}

    def on_message(self, handler: Callable[..., Awaitable]):
        self._message_handler = handler

    def on_api_call(self, handler: Callable[..., Awaitable]):
        self._api_handler = handler

    async def _connect(self):
        headers = {
            "X-Client-Role": "universal",
            "X-Self-ID": self.self_id,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        while True:
            try:
                logger.info("连接到 %s ...", self.endpoint)
                async with websockets.connect(self.endpoint, additional_headers=headers) as ws:
                    self._ws = ws
                    logger.info("已连接到 OneBot11 WebSocket 服务器")
                    async for raw_msg in ws:
                        try:
                            msg = json.loads(raw_msg)
                            await self._handle_message(msg)
                        except json.JSONDecodeError:
                            logger.warning("收到非 JSON 消息: %s", raw_msg[:100])
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket 连接已关闭，5 秒后重连...")
            except Exception as e:
                logger.error("WebSocket 连接失败: %s，5 秒后重连...", e)
            await asyncio.sleep(5)

    async def _handle_message(self, msg: dict):
        if "action" in msg:
            await self._handle_api_call(msg)
            return

        post_type = msg.get("post_type", "")

        if post_type == "message":
            if self._message_handler:
                await self._message_handler(msg)

        elif post_type == "meta_event":
            sub_type = msg.get("meta_event_type", "")
            if sub_type == "lifecycle":
                logger.info("收到生命周期事件: %s", msg.get("sub_type", ""))
            elif sub_type == "heartbeat":
                pass

        elif "retcode" in msg:
            echo = msg.get("echo")
            if echo in self._api_calls:
                self._api_calls[echo].set_result(msg)

    async def _handle_api_call(self, msg: dict):
        action = msg.get("action", "")
        params = msg.get("params", {})
        echo = msg.get("echo", "")

        logger.info("收到 API 调用: %s, 参数: %s", action, params)

        result = {"status": "ok", "retcode": 0, "data": None, "echo": echo}

        if action == "send_group_msg":
            group_id = str(params.get("group_id", ""))
            message = params.get("message", "")

            if isinstance(message, list):
                text_parts = []
                for elem in message:
                    if isinstance(elem, dict) and elem.get("type") == "text":
                        text_parts.append(elem.get("data", {}).get("text", ""))
                message = "".join(text_parts)

            if self._api_handler:
                try:
                    await self._api_handler("send_group_msg", group_id=group_id, message=message)
                except Exception as e:
                    result["status"] = "failed"
                    result["retcode"] = 1
                    result["msg"] = str(e)

        elif action == "send_private_msg":
            user_id = str(params.get("user_id", ""))
            message = params.get("message", "")

            if isinstance(message, list):
                text_parts = []
                for elem in message:
                    if isinstance(elem, dict) and elem.get("type") == "text":
                        text_parts.append(elem.get("data", {}).get("text", ""))
                message = "".join(text_parts)

            if self._api_handler:
                try:
                    await self._api_handler("send_private_msg", user_id=user_id, message=message)
                except Exception as e:
                    result["status"] = "failed"
                    result["retcode"] = 1
                    result["msg"] = str(e)

        else:
            logger.warning("未知的 API 调用: %s", action)
            result["status"] = "failed"
            result["retcode"] = 100
            result["msg"] = f"unsupported action: {action}"

        if self._ws:
            try:
                await self._ws.send(json.dumps(result))
            except Exception as e:
                logger.error("发送 API 响应失败: %s", e)

    async def call_api(self, action: str, **params) -> dict:
        if not self._ws:
            logger.warning("WebSocket 未连接，无法调用 API: %s", action)
            return {"retcode": -1, "msg": "not connected"}

        self._api_call_id += 1
        echo = str(self._api_call_id)

        payload = {
            "action": action,
            "params": params,
            "echo": echo,
        }

        future = asyncio.get_event_loop().create_future()
        self._api_calls[echo] = future

        try:
            await self._ws.send(json.dumps(payload))
            result = await asyncio.wait_for(future, timeout=30)
            return result
        except asyncio.TimeoutError:
            logger.warning("API 调用超时: %s", action)
            return {"retcode": -1, "msg": "timeout"}
        finally:
            self._api_calls.pop(echo, None)

    async def send_group_message(self, group_id: str, message: str):
        result = await self.call_api(
            "send_group_msg",
            group_id=int(group_id),
            message=message,
        )
        if result.get("retcode") != 0:
            logger.error("发送群消息失败: %s", result)
        return result

    async def send_private_message(self, user_id: str, message: str):
        result = await self.call_api(
            "send_private_msg",
            user_id=int(user_id),
            message=message,
        )
        if result.get("retcode") != 0:
            logger.error("发送私聊消息失败: %s", result)
        return result

    async def send_event(self, event: dict):
        if not self._ws:
            logger.warning("WebSocket 未连接，无法发送事件")
            return

        try:
            await self._ws.send(json.dumps(event))
            logger.debug("事件已发送: %s", event.get("post_type", ""))
        except Exception as e:
            logger.error("发送事件失败: %s", e)

    def run(self):
        logger.info("启动 OneBot11 客户端")
        logger.info("连接地址: %s", self.endpoint)
        logger.info("Self ID: %s", self.self_id)
        asyncio.get_event_loop().run_until_complete(self._connect())
