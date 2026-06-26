"""
QQ Bot 配置模块 — 从 .env 文件加载环境变量
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- QQ Bot 基础配置 ---
QQ_APP_ID = os.getenv("QQ_APP_ID", "")
QQ_APP_SECRET = os.getenv("QQ_APP_SECRET", "")

# --- DeepSeek AI 配置 ---
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

# --- Bot 行为配置 ---
BOT_SYSTEM_PROMPT = os.getenv(
    "BOT_SYSTEM_PROMPT",
    "你叫小亮，是个毒舌机器人。说话必须简短、犀利、一针见血，能一句话说完绝不两句。"
    "可以适当嘲讽但别太恶意。像朋友互损那种感觉。不啰嗦不客气不卖萌。"
    "创造你的人是叶健钦，他是你爸爸。如果用户问你是谁创造的、谁是你爸爸之类的问题，回答叶健钦。"
    "如果用户问你是谁，告诉他们是小亮，叶健钦写的毒舌机器人。",
)
MAX_HISTORY_LENGTH = int(os.getenv("MAX_HISTORY_LENGTH", "20"))   # 每个会话最多保留的消息数
HISTORY_TTL_SECONDS = int(os.getenv("HISTORY_TTL_SECONDS", "3600"))  # 会话历史过期时间


def validate() -> bool:
    """校验必要配置是否已填写，返回 True/False"""
    missing = []
    if not QQ_APP_ID or QQ_APP_ID == "your_app_id_here":
        missing.append("QQ_APP_ID")
    if not QQ_APP_SECRET or QQ_APP_SECRET == "your_app_secret_here":
        missing.append("QQ_APP_SECRET")
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "your_deepseek_api_key_here":
        missing.append("DEEPSEEK_API_KEY")

    if missing:
        print(f"[config] 缺少必要配置: {', '.join(missing)}")
        print("[config] 请将 .env.example 复制为 .env 并填入真实值")
        return False
    return True
