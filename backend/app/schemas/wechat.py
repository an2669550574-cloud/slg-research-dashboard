from typing import Optional

from pydantic import BaseModel


class WechatAccountOut(BaseModel):
    id: int
    name: str
    fakeid: str
    enabled: bool


class WechatAccountCreate(BaseModel):
    fakeid: str
    name: str


class WechatAccountUpdate(BaseModel):
    enabled: bool


class WechatAccountCandidate(BaseModel):
    """wechat-api searchbiz 按名搜出的候选号（供前端选「订阅哪个」）。

    不带头像：searchbiz 返回的 round_head_img 是 wechat-api 内网图片代理 URL，
    浏览器够不到，展示昵称 + alias 即可。
    """
    fakeid: str
    nickname: str
    alias: Optional[str] = None
