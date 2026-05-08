import json
import logging
import anthropic
from app.config import settings

logger = logging.getLogger(__name__)

MOCK_HISTORIES = {
    "com.lilithgames.rok": [
        {"event_date": "2018-09-17", "event_type": "launch", "title": "Rise of Kingdoms 全球公测上线", "description": "前身为 Civilizations: Rise to Power，正式以 Rise of Kingdoms 为名在全球 iOS/Android 上线，首月即进入美国策略榜 Top 10。"},
        {"event_date": "2019-03-01", "event_type": "marketing", "title": "启用 KOL 营销矩阵", "description": "与 YouTube 百万级频道合作，游戏实况和攻略视频累计播放量破亿，带动全球下载量环比增长 40%。"},
        {"event_date": "2019-11-01", "event_type": "version", "title": "文明系统大更新", "description": "新增多个文明选项，引入全新历史将领，月活用户突破 2000 万。"},
        {"event_date": "2020-07-01", "event_type": "revenue", "title": "累计收入突破 10 亿美元", "description": "成为 Lilith 旗下首款收入破 10 亿的产品，跻身全球手游收入 Top 20。"},
        {"event_date": "2021-06-01", "event_type": "ranking", "title": "连续 30 天登顶美国策略榜", "description": "借助赛季制内容更新和世界大战活动，在美国 App Store 策略分类连续 30 天排名第一。"},
        {"event_date": "2023-01-01", "event_type": "version", "title": "Lost Kingdom 全球同服新玩法", "description": "上线跨服战争新地图，日活峰值创历史新高，单月内购收入超 4000 万美元。"},
    ],
    "com.supercell.clashofclans": [
        {"event_date": "2012-08-02", "event_type": "launch", "title": "Clash of Clans 在芬兰上线内测", "description": "Supercell 在芬兰及加拿大进行软启动测试，积累核心用户和数据反馈。"},
        {"event_date": "2012-10-07", "event_type": "launch", "title": "全球正式上线 iOS", "description": "App Store 全球发布，首周即冲上多国免费榜榜首，成为现象级产品。"},
        {"event_date": "2013-10-07", "event_type": "version", "title": "Android 版本上线", "description": "登陆 Google Play，下载量在一个月内突破 5000 万。"},
        {"event_date": "2015-08-01", "event_type": "marketing", "title": "超级碗广告首次亮相", "description": "投放超级碗 60 秒广告，Liam Neeson 出镜，播出后 App Store 排名从第 6 跃升至第 1。"},
        {"event_date": "2016-01-01", "event_type": "revenue", "title": "年收入突破 15 亿美元", "description": "2015 全年营收达 15.3 亿美元，成为全球手游收入最高产品之一。"},
        {"event_date": "2021-04-01", "event_type": "version", "title": "Town Hall 14 与宠物系统上线", "description": "引入宠物系统重磅更新，老玩家回流率创近三年新高，MAU 环比增长 18%。"},
    ],
}

DEFAULT_HISTORY = [
    {"event_date": "2020-01-01", "event_type": "launch", "title": "游戏全球公测上线", "description": "产品在海外各大应用商店正式上线，开启全球市场推广。"},
    {"event_date": "2020-06-01", "event_type": "version", "title": "首次重大版本更新", "description": "新增核心玩法内容，用户留存率显著提升。"},
    {"event_date": "2021-03-01", "event_type": "ranking", "title": "进入美国策略榜 Top 20", "description": "产品在北美市场取得重大突破，排名持续攀升。"},
]


def _parse_json_array(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    return json.loads(text)


async def generate_history(app_id: str, game_name: str, publisher: str) -> list[dict]:
    """生成游戏发展历程。优先级：手工 mock → Claude API → 通用兜底。"""
    if app_id in MOCK_HISTORIES:
        return MOCK_HISTORIES[app_id]

    if not settings.ANTHROPIC_API_KEY:
        return DEFAULT_HISTORY

    prompt = f"""你是一名海外手游市场分析师。请为以下 SLG 游戏生成一份精简的发展历程时间线，要求：
1. 包含 5-8 个关键节点
2. 每个节点包含：日期(YYYY-MM-DD)、事件类型(launch/version/ranking/revenue/marketing)、标题(一句话)、描述(2-3句话)
3. 聚焦重大营销事件、版本里程碑、收入/排名突破
4. 输出 JSON 数组格式

游戏名称：{game_name}
发行商：{publisher}
App ID：{app_id}

直接输出 JSON 数组，不要其他内容。"""

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text
        events = _parse_json_array(text)
        if not isinstance(events, list) or not events:
            raise ValueError("model returned empty or non-list payload")
        return events
    except (anthropic.APIError, ValueError, KeyError, IndexError) as e:
        logger.warning("AI history generation failed for %s, falling back to default: %s", app_id, e)
        return DEFAULT_HISTORY
