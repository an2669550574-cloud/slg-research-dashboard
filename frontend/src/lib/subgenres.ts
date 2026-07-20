/** 玩法子品类受控词表（前端侧单一来源）。
 *
 * 与后端 `newcomer_i18n.SUBGENRE_VOCAB` 保持同步（后端是权威，改词表两边一起动）。
 * 消费方：我方产品页 match_subgenre 下拉（ProductsManage）+ 新品页人工锁定入口（NewReleases）。
 * 原本只在 ProductsManage 里定义，锁定入口上线后第三处复制会漂移，抽到这里。 */
export const SUBGENRE_OPTIONS = [
  '数字门SLG', '基地建设SLG', '国战SLG', '塔防', '三消合成',
  '城建模拟', '放置养成', '卡牌RPG', '休闲益智', '其他',
] as const
