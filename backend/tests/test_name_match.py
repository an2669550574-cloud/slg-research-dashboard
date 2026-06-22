"""name_match.corp_squash 归一原语：去法人后缀 + 拼接。"""
import pytest

from app.services.name_match import corp_squash


@pytest.mark.parametrize("tokens,expected", [
    (["topgames", "inc"], "topgames"),       # 去 inc
    (["top", "games"], "topgames"),           # 双方拼接落点一致 → 等值
    (["century", "games", "pte", "ltd"], "centurygames"),  # 多后缀
    (["funplus", "international", "ag"], "funplusinternational"),  # 只去 ag，保留描述词
    (["topgames"], "topgames"),               # camelCase 已连写
    (["inc"], ""),                            # 全是后缀 → 空（调用方据此跳过）
    ([], ""),                                  # 空
    (["co", "ltd"], ""),                      # 全后缀组合
])
def test_corp_squash(tokens, expected):
    assert corp_squash(tokens) == expected


def test_squash_does_not_strip_descriptive_words():
    """games / group / studio 等描述词**不**当后缀去，否则 "Top Games" 与 "Top Studio" 撞车。"""
    assert corp_squash(["top", "games"]) != corp_squash(["top", "studio"])
    assert corp_squash(["top", "games"]) == "topgames"
    assert corp_squash(["top", "studio"]) == "topstudio"
