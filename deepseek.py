"""
DeepSeek AI 对话模块 — 调用 DeepSeek API 生成回复，管理会话历史
"""
import time
import logging
from collections import OrderedDict

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class DeepSeekChat:
    """DeepSeek 对话客户端

    职责：
    1. 调用 DeepSeek API 生成 Chat Completion
    2. 管理多会话历史（内存缓存 + TTL 过期）
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        system_prompt: str = "你是一个友好的助手。",
        max_history: int = 20,
        history_ttl: int = 3600,
    ):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.system_prompt = system_prompt
        self.max_history = max_history
        self.history_ttl = history_ttl

        # 会话历史: {conversation_id: {"messages": [...], "last_access": timestamp}}
        self._conversations: OrderedDict[str, dict] = OrderedDict()

    # ── 会话管理 ──────────────────────────────────────────────

    def _get_or_create_history(self, conversation_id: str) -> list[dict]:
        """获取或创建会话消息列表，自动清理过期历史"""
        now = time.time()
        entry = self._conversations.get(conversation_id)

        # 检查是否过期
        if entry and (now - entry["last_access"] > self.history_ttl):
            del self._conversations[conversation_id]
            entry = None

        if entry is None:
            # 新建会话（LRU 淘汰）
            while len(self._conversations) >= 100:  # 最多缓存 100 个会话
                self._conversations.popitem(last=False)
            self._conversations[conversation_id] = {
                "messages": [],
                "last_access": now,
            }
            # 标记为 OrderedDict 最近使用的
            self._conversations.move_to_end(conversation_id)
        else:
            entry["last_access"] = now
            self._conversations.move_to_end(conversation_id)

        return self._conversations[conversation_id]["messages"]

    def _trim_history(self, messages: list[dict]) -> list[dict]:
        """裁剪消息历史，保留最近 N 轮对话（每轮 = user + assistant）"""
        max_messages = self.max_history * 2  # user + assistant 成对
        if len(messages) > max_messages:
            return messages[-max_messages:]
        return messages

    # ── 对话接口 ──────────────────────────────────────────────

    async def chat(
        self, conversation_id: str, user_message: str
    ) -> str:
        """
        发送消息并获取 AI 回复

        Args:
            conversation_id: 会话 ID（C2C 用 user_openid，群聊用 group_openid）
            user_message: 用户消息文本

        Returns:
            AI 回复文本
        """
        history = self._get_or_create_history(conversation_id)

        # 添加用户消息
        history.append({"role": "user", "content": user_message})
        history = self._trim_history(history)

        # 构建完整消息列表（system prompt + 历史）
        messages = [{"role": "system", "content": self.system_prompt}] + history
        logger.info(f"[{conversation_id[:12]}...] 调用 DeepSeek, 历史 {len(history)} 条")

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=2000,
                timeout=30,
            )
            reply = response.choices[0].message.content or "（我暂时不知道该说什么）"
        except Exception as e:
            logger.error(f"DeepSeek API 错误: {e}")
            reply = f"抱歉，AI 服务暂时不可用，请稍后再试。\n（错误: {type(e).__name__}）"

        # 保存 AI 回复到历史
        history.append({"role": "assistant", "content": reply})
        # 再次裁剪（user + assistant 各一条新增）
        history = self._trim_history(history)
        self._conversations[conversation_id]["messages"] = history

        return reply

    def clear_history(self, conversation_id: str) -> None:
        """清除指定会话的历史"""
        self._conversations.pop(conversation_id, None)
        logger.info(f"已清除会话 {conversation_id[:12]}... 的历史")
