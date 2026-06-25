"""
小亮 QQ Bot — 主入口

启动流程:
  python main.py

架构:
  QQ 服务器 ──WebSocket──▶ 接收事件 ──▶ 清洗消息 ──▶ DeepSeek AI
                                                          │
  QQ 服务器 ◀──HTTP POST── 发送回复 ◀──────────────  AI 生成回复
"""
import json
import re
import asyncio
import logging
import signal
from typing import Optional

import websockets

from config import (
    QQ_APP_ID,
    QQ_APP_SECRET,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    BOT_SYSTEM_PROMPT,
    MAX_HISTORY_LENGTH,
    HISTORY_TTL_SECONDS,
    validate,
)
from qq_api import QQBotAPI
from deepseek import DeepSeekChat

# ── 日志 ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bot")

# ── 全局状态 ─────────────────────────────────────────────────
shutdown_flag = False          # 退出信号


def clean_message_content(content: str) -> str:
    """清洗消息内容：去除 @机器人 的 mention 标记和首尾空白"""
    # 移除 <@!123456> 或 <@123456> 格式的 @mention
    cleaned = re.sub(r"<@!?\d+>", "", content)
    # 移除可能残留的多余空白
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def is_empty_message(content: str) -> bool:
    """判断清洗后的消息是否为空（只是 @ 了一下，没说话）"""
    return len(clean_message_content(content)) == 0


class XiaoliangBot:
    """小亮 Bot 主控制器"""

    def __init__(self):
        self.qq = QQBotAPI(app_id=QQ_APP_ID, app_secret=QQ_APP_SECRET)
        self.ai = DeepSeekChat(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            model=DEEPSEEK_MODEL,
            system_prompt=BOT_SYSTEM_PROMPT,
            max_history=MAX_HISTORY_LENGTH,
            history_ttl=HISTORY_TTL_SECONDS,
        )
        self.ws = None
        self.heartbeat_interval = 0
        self.session_id = ""
        self._last_seq: Optional[int] = None

    # ── Heartbeat ─────────────────────────────────────────

    async def heartbeat_loop(self):
        """心跳维持任务 — 按 heartbeat_interval 周期发送 OpCode 1"""
        while not shutdown_flag:
            try:
                await asyncio.sleep(self.heartbeat_interval / 1000)
                if self.ws and not shutdown_flag:
                    seq = self._last_seq
                    await self.ws.send(json.dumps({"op": 1, "d": seq}))
                    logger.debug(f"♡ 心跳 ping (seq={seq})")
            except Exception as e:
                logger.warning(f"心跳发送失败: {e}")

    # ── 事件处理 ──────────────────────────────────────────

    async def handle_c2c_message(self, data: dict):
        """处理 C2C（私聊）消息"""
        msg_id = data.get("id", "")
        author = data.get("author", {})
        user_openid = author.get("id", "") or author.get("user_openid", "")
        content = data.get("content", "")
        cleaned = clean_message_content(content)

        logger.info(f"📩 C2C 消息 | user={user_openid[:12]}... | content={cleaned[:50]}")

        if is_empty_message(content):
            return

        reply = await self.ai.chat(conversation_id=user_openid, user_message=cleaned)
        await self.qq.send_c2c_message(openid=user_openid, content=reply, msg_id=msg_id)

    async def handle_group_at_message(self, data: dict):
        """处理群聊 @机器人 消息"""
        msg_id = data.get("id", "")
        group_openid = data.get("group_openid", "") or data.get("group_id", "")
        author = data.get("author", {})
        user_openid = author.get("id", "") or author.get("member_openid", "")
        content = data.get("content", "")
        cleaned = clean_message_content(content)

        logger.info(
            f"📢 群聊@消息 | group={group_openid[:12]}... | "
            f"user={user_openid[:12]}... | content={cleaned[:50]}"
        )

        if is_empty_message(content):
            await self.qq.send_group_message(
                group_openid=group_openid,
                content="你@我了，但没说话哦～有什么可以帮你的吗？",
                msg_id=msg_id,
            )
            return

        reply = await self.ai.chat(conversation_id=group_openid, user_message=cleaned)
        await self.qq.send_group_message(
            group_openid=group_openid, content=reply, msg_id=msg_id
        )

    async def dispatch(self, event: dict):
        """事件分发 — 根据事件类型路由到对应处理函数"""
        event_type = event.get("t", "")
        data = event.get("d", {})

        # 更新序列号（用于心跳和 Resume）
        if "s" in event and event["s"] is not None:
            self._last_seq = event["s"]

        handlers = {
            "C2C_MESSAGE_CREATE":       self.handle_c2c_message,
            "GROUP_AT_MESSAGE_CREATE":  self.handle_group_at_message,
            "FRIEND_ADD":               lambda d: logger.info(f"👤 新好友: {d.get('author', {}).get('id', '?')}"),
            "FRIEND_DEL":               lambda d: logger.info(f"👋 好友删除: {d.get('author', {}).get('id', '?')}"),
            "GROUP_ADD_ROBOT":          lambda d: logger.info(f"➕ 被加入群: {d.get('group_openid', '?')}"),
            "GROUP_DEL_ROBOT":          lambda d: logger.info(f"➖ 被移出群: {d.get('group_openid', '?')}"),
        }

        handler = handlers.get(event_type)
        if handler:
            try:
                await handler(data)
            except Exception as e:
                logger.error(f"处理事件 {event_type} 出错: {e}", exc_info=True)
        else:
            logger.debug(f"忽略事件: {event_type}")

    # ── 主循环 ────────────────────────────────────────────

    async def run(self):
        """启动 Bot — 连接 → 鉴权 → 事件循环"""
        global shutdown_flag

        # 连接 + 鉴权
        logger.info("正在连接到 QQ Bot 网关...")
        self.ws, self.heartbeat_interval, self.session_id = await self.qq.connect()
        logger.info(f"✅ Bot 已上线！Session: {self.session_id[:16]}...")

        # 启动心跳任务
        heartbeat_task = asyncio.create_task(self.heartbeat_loop())

        # 事件处理循环
        try:
            async for raw in self.ws:
                if shutdown_flag:
                    break
                try:
                    event = json.loads(raw)
                    op = event.get("op")

                    if op == 0:  # Dispatch — 业务事件
                        await self.dispatch(event)

                    elif op == 11:  # Heartbeat ACK
                        logger.debug("♡ 心跳 pong")

                    elif op == 7:  # Reconnect — 服务器要求重连
                        logger.warning("服务器要求重连 (OpCode 7)")
                        break

                    elif op == 9:  # Invalid Session
                        logger.error("鉴权失败 (OpCode 9)！请检查 AppID/AppSecret")
                        break

                    elif op == 10:  # Hello (一般只在连接时发送一次)
                        logger.info("再次收到 Hello, 心跳间隔已更新")
                        self.heartbeat_interval = event["d"]["heartbeat_interval"]

                    else:
                        logger.debug(f"未处理的 OpCode: {op}")

                except json.JSONDecodeError:
                    logger.warning(f"无法解析的消息: {raw[:200]}")

        except asyncio.CancelledError:
            pass
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"WebSocket 连接断开: {e}")
        finally:
            shutdown_flag = True
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            logger.info("Bot 已下线")


# ── 入口 ──────────────────────────────────────────────────────

def main():
    """程序入口 — 带重连的启动循环"""
    if not validate():
        return

    bot = XiaoliangBot()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 注册 Ctrl+C 信号处理
    def signal_handler():
        global shutdown_flag
        logger.info("收到退出信号，正在关闭...")
        shutdown_flag = True
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows 上 add_signal_handler 对 SIGTERM 不可用
            pass

    # 带重试的启动循环
    retry_delay = 1
    max_delay = 60

    while not shutdown_flag:
        try:
            loop.run_until_complete(bot.run())
        except Exception as e:
            logger.error(f"Bot 运行异常: {e}", exc_info=True)

        if not shutdown_flag:
            logger.info(f"将在 {retry_delay} 秒后重连...")
            loop.run_until_complete(asyncio.sleep(retry_delay))
            retry_delay = min(retry_delay * 2, max_delay)
        else:
            break

    loop.close()
    logger.info("程序已退出")


if __name__ == "__main__":
    main()
