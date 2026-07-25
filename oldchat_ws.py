"""
oldchat_ws.py - OldChat WebSocket 客户端
实时接收消息，替代轮询
"""

import asyncio
import base64
import hashlib
import hmac as hmac_mod
import json
import logging
import os
import time
from typing import Callable, Awaitable, Optional

import websockets
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger("oldchat_ws")


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if pad < 1 or pad > 16:
        return data
    return data[:-pad]


class OldChatWS:
    def __init__(self, base_url: str, access_token: str):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self._message_handler: Optional[Callable] = None
        self._ws = None
        self._session_id: Optional[str] = None
        self._aes_key: Optional[bytes] = None
        self._hmac_key: Optional[bytes] = None

    def on_message(self, handler: Callable[..., Awaitable]):
        self._message_handler = handler

    async def _handshake(self):
        ec_private = ec.generate_private_key(ec.SECP256R1(), default_backend())
        ec_public = ec_private.public_key()

        client_pub_bytes = ec_public.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        client_pub_b64 = base64.b64encode(client_pub_bytes).decode("ascii")

        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/v1/auth/handshake",
                json={"client_pub": client_pub_b64}
            ) as resp:
                data = await resp.json()

        server_pub_bytes = base64.b64decode(data["server_pub"])
        server_pub = serialization.load_der_public_key(server_pub_bytes, default_backend())

        shared_secret = ec_private.exchange(ec.ECDH(), server_pub)
        raw = shared_secret[-32:] if len(shared_secret) > 32 else shared_secret.zfill(32)

        self._aes_key = hashlib.sha256(raw + b"enc").digest()
        self._hmac_key = hashlib.sha256(raw + b"mac").digest()
        self._session_id = data["session_id"]

        logger.info("ECDH 握手完成, session_id=%s", self._session_id[:8])

    async def _decrypt(self, payload: str) -> Optional[str]:
        try:
            env = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None

        if not all(k in env for k in ("iv", "data", "mac")):
            return None

        iv = base64.b64decode(env["iv"])
        ciphertext = base64.b64decode(env["data"])
        mac = base64.b64decode(env["mac"])

        expected = hmac_mod.new(self._hmac_key, iv + ciphertext, hashlib.sha256).digest()
        if not hmac_mod.compare_digest(mac, expected):
            logger.warning("HMAC 验证失败")
            return None

        cipher = Cipher(algorithms.AES(self._aes_key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        plain = _pkcs7_unpad(padded)
        return plain.decode("utf-8", errors="replace")

    async def _connect(self):
        while True:
            try:
                await self._handshake()
                ws_url = f"ws://{self.base_url.replace('http://', '')}/v1/ws?token={self.access_token}&sid={self._session_id}"

                logger.info("连接 OldChat WebSocket: %s", ws_url.split("?")[0])
                async with websockets.connect(ws_url) as ws:
                    self._ws = ws
                    logger.info("已连接到 OldChat WebSocket")
                    async for raw_msg in ws:
                        try:
                            plain = await self._decrypt(raw_msg)
                            if not plain:
                                continue
                            msg = json.loads(plain)
                            if self._message_handler:
                                await self._message_handler(msg)
                        except json.JSONDecodeError:
                            pass
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket 连接已关闭，3 秒后重连...")
            except Exception as e:
                logger.error("WebSocket 连接失败: %s，3 秒后重连...", e)

            await asyncio.sleep(3)

    def run(self):
        asyncio.get_event_loop().run_until_complete(self._connect())
