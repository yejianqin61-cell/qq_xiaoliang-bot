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
from fortune import draw_fortune, is_fortune_request
from skills import (
    draw_rp, is_rp_request,
    get_weather, is_weather_request,
    is_translate_request, parse_translate,
    is_daddy_request, DADDY_REPLY,
    is_huo_request, HUO_REPLY,
    is_grandpa_request, GRANDPA_REPLY,
    is_sai_request, SAI_REPLY,
)

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

    async def _route_message(self, user_id: str, group_id: str, content: str, cleaned: str) -> str:
        """统一消息路由：按优先级匹配技能，都不命中则走 AI"""
        # 优先级 0：空消息
        if is_empty_message(content):
            return "你@我了，但没说话——有话快说。"

        # 优先级 1：身份问答（硬编码，不调 AI）
        if is_daddy_request(cleaned):
            return DADDY_REPLY

        # 优先级 1.5：整活
        if is_huo_request(cleaned):
            return HUO_REPLY

        # 优先级 1.6：爷爷
        if is_grandpa_request(cleaned):
            return GRANDPA_REPLY

        # 优先级 1.7：塞林木
        if is_sai_request(cleaned):
            return SAI_REPLY

        # 优先级 2：运势抽卡
        if is_fortune_request(cleaned):
            return draw_fortune(user_id)

        # 优先级 3：今日人品
        if is_rp_request(cleaned):
            return draw_rp(user_id)

        # 优先级 4：天气查询
        if is_weather_request(cleaned):
            return await get_weather(cleaned)

        # 优先级 5：翻译
        if is_translate_request(cleaned):
            text, target = parse_translate(cleaned)
            return await self._translate(text, target)

        # 默认：AI 对话
        conv_id = group_id if group_id else user_id
        return await self.ai.chat(conversation_id=conv_id, user_message=cleaned)

    async def _translate(self, text: str, target: str) -> str:
        """翻译——单次调用 DeepSeek，不写入会话历史"""
        target_lang = "英文" if target == "english" else "中文"
        try:
            resp = await self.ai.client.chat.completions.create(
                model=self.ai.model,
                messages=[
                    {"role": "system", "content": f"你是翻译助手。把用户输入翻译成{target_lang}，只输出译文，不要任何解释。"},
                    {"role": "user", "content": text},
                ],
                temperature=0.3,
                max_tokens=2000,
                timeout=30,
            )
            result = resp.choices[0].message.content or "翻译失败"
            return f"🔤 翻译结果（{target_lang}）：\n{result}"
        except Exception as e:
            return f"翻译炸了，换个说法试试。\n（{type(e).__name__}）"

    async def handle_c2c_message(self, data: dict):
        """处理 C2C（私聊）消息"""
        msg_id = data.get("id", "")
        author = data.get("author", {})
        user_openid = author.get("id", "") or author.get("user_openid", "")
        content = data.get("content", "")
        cleaned = clean_message_content(content)

        logger.info(f"📩 C2C 消息 | user={user_openid[:12]}... | content={cleaned[:50]}")

        reply = await self._route_message(user_openid, "", content, cleaned)
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

        reply = await self._route_message(user_openid, group_openid, content, cleaned)

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
