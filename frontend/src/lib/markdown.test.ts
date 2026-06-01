import { describe, it, expect } from 'vitest'
import { unifiedToMarkdown, type UnifiedData } from './markdown'

describe('unifiedToMarkdown', () => {
  it('renders title, meta line and a trailing newline', () => {
    const md = unifiedToMarkdown({}, { model: 'claude-sonnet-4.5', cost: 0.1234 })
    expect(md.startsWith('# 跨素材统一创意方向\n')).toBe(true)
    expect(md).toContain('模型：claude-sonnet-4.5')
    expect(md).toContain('成本：$0.1234')
    expect(md.endsWith('\n')).toBe(true)
  })

  it('includes the product brief section when provided', () => {
    const md = unifiedToMarkdown({}, { productBrief: '末日丧尸生存 SLG' })
    expect(md).toContain('## 自家产品 brief')
    expect(md).toContain('末日丧尸生存 SLG')
  })

  it('omits empty sections (no common patterns, no directions)', () => {
    const md = unifiedToMarkdown({})
    expect(md).not.toContain('## 共性结构')
    expect(md).not.toContain('## 迁移方向')
  })

  it('renders common patterns and joins shared hooks', () => {
    const data: UnifiedData = {
      common_patterns: {
        shared_structure: '强钩子开场',
        shared_hooks: ['对比', '反转'],
        shared_pacing: '快切',
      },
    }
    const md = unifiedToMarkdown(data)
    expect(md).toContain('## 共性结构')
    expect(md).toContain('- **底层结构**：强钩子开场')
    expect(md).toContain('- **共性钩子**：对比、反转')
    expect(md).toContain('- **节奏共性**：快切')
  })

  it('numbers directions and flattens key hooks', () => {
    const data: UnifiedData = {
      directions: [
        {
          name: '极寒求生',
          concept: '在冰天雪地里建立据点',
          opening_3sec: '暴风雪逼近',
          key_hooks: [{ ts_est: '0-1s', kind: '危机', note: '冻死倒计时' }],
          risk_notes: '别用 CG',
        },
      ],
    }
    const md = unifiedToMarkdown(data)
    expect(md).toContain('### 01 极寒求生')
    expect(md).toContain('在冰天雪地里建立据点')
    expect(md).toContain('- **0-3s**：暴风雪逼近')
    expect(md).toContain('- **关键钩子**：0-1s 危机 冻死倒计时')
    expect(md).toContain('- ⚠ **风险提示**：别用 CG')
  })
})
