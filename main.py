"""
main.py - crbot-onebot11 入口
OldChat ↔ OneBot11 协议桥接器
"""

import asyncio
import json
import logging
import sys
import threading
import time
from pathlib import Path

from oldchat_client import OldChatClient
from oldchat_ws import OldChatWS
from onebot_client import OneBotClient
from bridge import Bridge

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("crbot")


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.json"
    if not config_path.exists():
        default_config = {
            "oldchat": {
                "base_url": "http://60.205.94.101:8080",
                "identifier": "YOUR_BOT_NAME",
                "password": "YOUR_PASSWORD",
                "use_encryption": True,
                "groups": ["GRP-3TTO6Q"],
                "poll_interval": 3
            },
            "onebot11": {
                "endpoint": "ws://127.0.0.1:5521/ws",
                "self_id": "YOUR_SELF_ID",
                "token": None
            },
            "bridge": {
                "group_mapping": {
                    "GRP-3TTO6Q": 0
                }
            }
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4, ensure_ascii=False)
        print("config.json 不存在，已生成默认配置文件，请填写后重新运行。")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _uptime_str(start: float) -> str:
    elapsed = time.time() - start
    d = int(elapsed) // 86400
    h = (int(elapsed) % 86400) // 3600
    m = (int(elapsed) % 3600) // 60
    s = int(elapsed) % 60
    parts = []
    if d:
        parts.append(f"{d}天")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}min")
    parts.append(f"{s}s")
    return " ".join(parts)


def main():
    started_at = time.time()
    config = load_config()

    banner = (
        f"CRBot-OneBot11 Running: {_uptime_str(started_at)}"
    )

    oldchat_cfg = config["oldchat"]
    onebot_cfg = config["onebot11"]
    bridge_cfg = config.get("bridge", {})

    oldchat = OldChatClient(oldchat_cfg)
    try:
        oldchat.login(oldchat_cfg["identifier"], oldchat_cfg["password"])
    except Exception as e:
        logger.error("OldChat 登录失败: %s", e)
        sys.exit(1)

    endpoint = onebot_cfg.get("endpoint", "ws://127.0.0.1:5521/ws")
    self_id = onebot_cfg.get("self_id", "YOUR_SELF_ID")

    onebot = OneBotClient(
        endpoint=endpoint,
        self_id=self_id,
        token=onebot_cfg.get("token"),
    )

    bridge = Bridge(oldchat, onebot, bridge_cfg)

    onebot.on_message(bridge.onebot_to_oldchat)
    onebot.on_api_call(bridge.handle_api_call)

    logger.info("crbot-onebot11 启动完成")
    logger.info("  OldChat: %s", oldchat_cfg["base_url"])
    logger.info("  OneBot11: %s", endpoint)

    loop = asyncio.new_event_loop()
    bridge.set_loop(loop)
    asyncio.set_event_loop(loop)

    oldchat_ws = OldChatWS(oldchat_cfg["base_url"], oldchat.access_token)
    oldchat_ws.on_message(bridge.oldchat_ws_handler)

    async def run_all():
        await asyncio.gather(
            oldchat_ws._connect(),
            onebot._connect(),
        )

    loop.run_until_complete(run_all())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("crbot-onebot11 已停止")
