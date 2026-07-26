"""
avatar_server.py - 头像代理服务器
通过假 QQ 号查询 OldChat 头像 URL
"""

import logging
from aiohttp import web

logger = logging.getLogger("avatar_server")


class AvatarServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 5522):
        self.host = host
        self.port = port
        self._oldchat = None
        self._qq_to_uid = {}
        self._avatar_cache = {}

    def set_oldchat(self, oldchat):
        self._oldchat = oldchat

    def update_mapping(self, qq_to_uid: dict):
        self._qq_to_uid.update(qq_to_uid)

    async def _handle_avatar(self, request: web.Request) -> web.Response:
        qq_str = request.match_info.get("qq", "")
        try:
            qq = int(qq_str)
        except ValueError:
            return web.json_response({"error": "invalid qq"}, status=400)

        uid = self._qq_to_uid.get(qq)
        if not uid:
            return web.json_response({"error": "user not found"}, status=404)

        if qq in self._avatar_cache:
            return web.json_response({"url": self._avatar_cache[qq]})

        if not self._oldchat:
            return web.json_response({"error": "oldchat not connected"}, status=503)

        try:
            data = self._oldchat._request("GET", "/v1/users/profile", params={"uid": uid})
            avatar = data.get("avatar_url", "")
            if avatar and avatar.startswith("/"):
                avatar = self._oldchat.base_url + avatar
            self._avatar_cache[qq] = avatar
            return web.json_response({"url": avatar})
        except Exception as e:
            logger.error("获取头像失败: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_redirect(self, request: web.Request) -> web.Response:
        qq_str = request.match_info.get("qq", "")
        try:
            qq = int(qq_str)
        except ValueError:
            return web.Response(text="invalid qq", status=400)

        uid = self._qq_to_uid.get(qq)
        if not uid or not self._oldchat:
            return web.Response(text="not found", status=404)

        try:
            data = self._oldchat._request("GET", "/v1/users/profile", params={"uid": uid})
            avatar = data.get("avatar_url", "")
            if avatar and avatar.startswith("/"):
                avatar = self._oldchat.base_url + avatar
            if avatar:
                raise web.HTTPFound(avatar)
        except web.HTTPFound:
            raise
        except Exception:
            pass

        return web.Response(text="not found", status=404)

    def start(self):
        app = web.Application()
        app.router.add_get("/avatar/{qq}.json", self._handle_avatar)
        app.router.add_get("/avatar/{qq}", self._handle_redirect)

        async def run():
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
            logger.info("头像代理服务器启动: http://%s:%s/avatar/{qq}", self.host, self.port)
            await asyncio.Event().wait()

        return run()
