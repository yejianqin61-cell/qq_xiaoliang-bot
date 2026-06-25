"""
QQ Bot API 封装 — 负责 AccessToken、WebSocket 连接、消息收发
"""
import json
import time
import logging
from typing import Optional, Tuple

import httpx
import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)

# ── API 端点常量 ─────────────────────────────────────────────
AUTH_URL   = "https://bots.qq.com/app/getAppAccessToken"
GATEWAY_URL = "https://api.sgroup.qq.com/gateway"
API_BASE   = "https://api.sgroup.qq.com"

# Intents 位掩码
# GROUP_AND_C2C_EVENT (1<<25): 群聊@ + 私聊 + 好友添加等
INTENT_GROUP_AND_C2C = 1 << 25
# 如果需要频道消息，可追加: INTENT_PUBLIC_GUILD_MSG = 1 << 30
DEFAULT_INTENTS = INTENT_GROUP_AND_C2C


class QQBotAPI:
    """QQ Bot API 客户端

    职责：
    1. 获取并缓存 AccessToken（有效期 7200 秒）
    2. 获取 WebSocket 网关地址
    3. 通过 WebSocket 接收事件
    4. 通过 HTTP API 发送消息
    """

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # ── Token 管理 ───────────────────────────────────────────

    async def get_access_token(self) -> str:
        """获取 AccessToken（带缓存，过期自动刷新）"""
        if self._token and time.time() < self._token_expires_at - 300:
            return self._token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                AUTH_URL,
                json={"appId": self.app_id, "clientSecret": self.app_secret},
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            data = resp.json()
            logger.info(f"获取 AccessToken 响应: {resp.status_code}")

            if "access_token" not in data:
                logger.error(f"获取 AccessToken 失败: {data}")
                raise RuntimeError(f"QQ API 返回错误: {data.get('code', '?')} - {data.get('message', data)}")

            self._token = data["access_token"]
            self._token_expires_at = time.time() + int(data.get("expires_in", 7200))
            logger.info(f"AccessToken 已刷新，有效期至 "
                        f"{time.strftime('%H:%M:%S', time.localtime(self._token_expires_at))}")
            return self._token

    # ── 网关地址 ──────────────────────────────────────────────

    async def get_gateway_url(self) -> str:
        """获取 WebSocket 网关地址"""
        token = await self.get_access_token()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                GATEWAY_URL,
                headers={
                    "Authorization": f"QQBot {token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            data = resp.json()
            url = data["url"]
            logger.info(f"网关地址: {url}")
            return url

    # ── 消息发送 ──────────────────────────────────────────────

    async def send_c2c_message(
        self, openid: str, content: str, msg_id: Optional[str] = None
    ) -> dict:
        """发送 C2C（私聊）消息"""
        token = await self.get_access_token()
        body = {
            "content": content,
            "msg_type": 0,  # 文本消息
        }
        if msg_id:
            body["msg_id"] = msg_id  # 引用回复

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_BASE}/v2/users/{openid}/messages",
                json=body,
                headers={
                    "Authorization": f"QQBot {token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            result = resp.json()
            logger.info(f"发送 C2C 消息 -> {openid}: {resp.status_code}")
            return result

    async def send_group_message(
        self, group_openid: str, content: str, msg_id: Optional[str] = None
    ) -> dict:
        """发送群聊消息"""
        token = await self.get_access_token()
        body = {
            "content": content,
            "msg_type": 0,
        }
        if msg_id:
            body["message_reference"] = {
                "message_id": msg_id,
                "ignore_get_message_error": True,
            }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_BASE}/v2/groups/{group_openid}/messages",
                json=body,
                headers={
                    "Authorization": f"QQBot {token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            result = resp.json()
            logger.info(f"发送群聊消息 -> {group_openid}: {resp.status_code}")
            return result

    # ── WebSocket 连接 ────────────────────────────────────────

    async def connect(self) -> Tuple[ClientConnection, int, str]:
        """建立 WebSocket 连接并完成鉴权，返回 (ws, heartbeat_interval_ms, session_id)"""
        # 1. 获取网关地址
        ws_url = await self.get_gateway_url()
        token = await self.get_access_token()

        # 2. 建立 WebSocket 连接
        ws = await websockets.connect(ws_url, max_size=2**20)
        logger.info("WebSocket 已连接")

        # 3. 接收 Hello（OpCode 10）
        hello = json.loads(await ws.recv())
        if hello.get("op") != 10:
            raise RuntimeError(f"期望 OpCode 10 (Hello)，实际收到: {hello}")
        heartbeat_interval = hello["d"]["heartbeat_interval"]
        logger.info(f"收到 Hello，心跳间隔: {heartbeat_interval}ms")

        # 4. 发送 Identify（OpCode 2）
        identify_payload = {
            "op": 2,
            "d": {
                "token": f"QQBot {token}",
                "intents": DEFAULT_INTENTS,
                "shard": [0, 1],
                "properties": {},
            },
        }
        await ws.send(json.dumps(identify_payload))
        logger.info("已发送 Identify")

        # 5. 接收 Ready 事件
        ready = json.loads(await ws.recv())
        if ready.get("t") != "READY":
            raise RuntimeError(f"期望 READY 事件，实际收到: {ready}")
        session_id = ready["d"]["session_id"]
        logger.info(f"鉴权成功！Session ID: {session_id}  Bot: {ready['d'].get('user', {}).get('username', '?')}")

        return ws, heartbeat_interval, session_id
