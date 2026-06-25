"""
技能模块 — 人品、天气、翻译、身份问答
所有不需要 AI 的本地功能都放这里
"""
import random
import hashlib
from datetime import datetime, timezone, timedelta

import httpx

CST = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════════════════════
# 共享工具
# ═══════════════════════════════════════════════════════════


def _day_seed(user_id: str) -> int:
    """每人每天固定的随机种子"""
    today = datetime.now(CST).strftime("%Y-%m-%d")
    seed_str = f"{user_id}:{today}"
    return int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)


# ═══════════════════════════════════════════════════════════
# 1. 今日人品
# ═══════════════════════════════════════════════════════════

def draw_rp(user_id: str) -> str:
    """基于 user_id + 日期的人品值，每天固定"""
    seed = _day_seed(user_id)
    rng = random.Random(seed)
    score = rng.randint(0, 100)

    # 毒舌点评
    if score >= 95:
        roast = "你不是人，你是神仙。但就今天。"
    elif score >= 80:
        roast = "今天做个人了，保持住。"
    elif score >= 60:
        roast = "及格线以上，对你来说算超常发挥。"
    elif score >= 40:
        roast = "也就这样了，别挣扎，躺好。"
    elif score >= 20:
        roast = "建议今天少说话，多微笑——虽然你笑起来更吓人。"
    elif score >= 5:
        roast = "你今天是来地球凑数的。"
    else:
        roast = "建议关机重启人生。没救了。等明天吧。"

    bar_len = 20
    filled = int(score / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)

    today = datetime.now(CST).strftime("%Y-%m-%d")
    return (
        f"════ 今日人品 • {today} ════\n\n"
        f"📊 人品值：【{score}分】\n"
        f"[{bar}]\n\n"
        f"💀 小亮锐评：{roast}\n"
    )


RP_KEYWORDS = ["人品", "运气值", "人品值", "今日人品", "我的人品"]


def is_rp_request(text: str) -> bool:
    return any(kw in text for kw in RP_KEYWORDS)


# ═══════════════════════════════════════════════════════════
# 2. 天气查询（免费 API wttr.in，无需 Key）
# ═══════════════════════════════════════════════════════════

WEATHER_KEYWORDS = ["天气", "气温", "下雨"]


def is_weather_request(text: str) -> bool:
    return any(kw in text for kw in WEATHER_KEYWORDS)


def _extract_city(text: str) -> str:
    """从消息中提取城市名"""
    for kw in WEATHER_KEYWORDS:
        text = text.replace(kw, "")
    # 去掉常见辅助词
    for noise in ["吗", "呢", "啊", "吧", "怎么样", "如何", "今天", "明天", "现在", "的", "了", "查", "查询", "帮我", "一下", "了"]:
        text = text.replace(noise, "")
    city = text.strip()
    return city if city else "Beijing"


async def get_weather(text: str) -> str:
    """查天气 — 调用 wttr.in 免费接口"""
    city = _extract_city(text)
    url = f"https://wttr.in/{city}?format=%l:+%c+%t,+%h,+%w&lang=zh"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            result = resp.text.strip()

        if not result or result.startswith("ERROR"):
            return f"查不到「{city}」的天气，你确定这地方在地球上？"

        # 城市名有时会返回冗余信息，清理
        # 结果格式: "Beijing: ☀️ +25°C, 湿度 60%, 风速 15km/h"
        return (
            f"════ {city} 天气 ════\n\n"
            f"{result}\n\n"
            f"💀 天气报完了。该穿啥自己看着办。"
        )
    except Exception as e:
        return f"天气接口抽风了，可能是老天爷不想让你知道。\n（{type(e).__name__}）"


# ═══════════════════════════════════════════════════════════
# 3. 翻译（中英互译，走 DeepSeek）
# ═══════════════════════════════════════════════════════════

TRANSLATE_KEYWORDS = ["翻译", "翻译成", "用英文说", "用中文说"]


def is_translate_request(text: str) -> bool:
    return any(kw in text for kw in TRANSLATE_KEYWORDS)


def parse_translate(text: str) -> tuple[str, str]:
    """
    解析翻译请求，返回 (待翻译文本, 目标语言)
    目标语言: "english" | "chinese"
    """
    # 判断目标语言
    if "成英文" in text or "成英语" in text or "用英文" in text:
        target = "english"
    elif "成中文" in text or "用中文" in text:
        target = "chinese"
    else:
        # 默认：检测语言，中文翻英文，英文翻中文
        text_to_check = text.replace("翻译", "").strip()
        has_chinese = any("一" <= c <= "鿿" for c in text_to_check)
        target = "english" if has_chinese else "chinese"

    # 提取待翻译文本
    text_part = text
    for kw in ["翻译成英文", "翻译成中文", "翻译成英语", "用英文说", "用中文说", "翻译"]:
        text_part = text_part.replace(kw, "")
    text_part = text_part.strip().lstrip("：:").strip()

    if not text_part:
        text_part = "你啥也没给我，我翻译空气？"

    return text_part, target


# ═══════════════════════════════════════════════════════════
# 4. 身份问答（硬编码：叶健钦是爸爸）
# ═══════════════════════════════════════════════════════════

DADDY_KEYWORDS = [
    "谁是你爸爸", "谁造的你", "谁发明的你", "谁开发的你",
    "你的主人", "你爸是谁", "你是谁造的", "你的创造者",
    "谁创造了你", "你的作者", "你爹是谁", "你的开发者",
    "谁写的你", "你是谁写的",
]


def is_daddy_request(text: str) -> bool:
    return any(kw in text for kw in DADDY_KEYWORDS)


DADDY_REPLY = (
    "我爸爸是叶健钦。\n"
    "你有事找他别找我，我只是一段代码，他是写代码的那个人。"
)


# ═══════════════════════════════════════════════════════════
# 5. 活王 — 给大家整个活
# ═══════════════════════════════════════════════════════════

HUO_KEYWORDS = ["给大家整个活", "整个活", "整个活儿"]

HUO_REPLY = "3，2，1，走！忽略！"


def is_huo_request(text: str) -> bool:
    return any(kw in text for kw in HUO_KEYWORDS)


# ═══════════════════════════════════════════════════════════
# 6. 爷爷 — 叶木全 / 叶栓
# ═══════════════════════════════════════════════════════════

GRANDPA_KEYWORDS = ["叶木全", "叶栓"]

GRANDPA_REPLY = "他是我爷爷，一名神医。"


def is_grandpa_request(text: str) -> bool:
    return any(kw in text for kw in GRANDPA_KEYWORDS)


# ═══════════════════════════════════════════════════════════
# 7. 塞林木 — 闽南语对线
# ═══════════════════════════════════════════════════════════

SAI_KEYWORDS = ["塞林木","塞林木啦","我塞林老木"]

SAI_REPLY = "赶羚羊啦，你这个老B灯"


def is_sai_request(text: str) -> bool:
    return any(kw in text for kw in SAI_KEYWORDS)


# ═══════════════════════════════════════════════════════════
# 8. 妈妈 — 妈妈给我发把枪
# ═══════════════════════════════════════════════════════════

MOM_KEYWORDS = ["你妈妈是谁", "谁是你妈妈", "你妈是谁", "谁是你妈", "你妈妈是", "你的妈妈"]


def is_mom_request(text: str) -> bool:
    return any(kw in text for kw in MOM_KEYWORDS)


MOM_REPLY = "妈妈给我发把枪，妈妈，妈妈~"
