// ESLint flat config — 最小聚焦：只防 React hooks 规则违反。
//
// 为什么只有这一条规则：hooks 写在 early return 之后 → prop 切换时 hook 数量变化崩页，
// 是 tsc + vitest 都抓不到、本项目反复踩的唯一崩页类 bug（见 CLAUDE.md「React hooks 顺序」）。
// rules-of-hooks 正是为它设计的静态网。故意**不引** typescript-eslint 全套规则：16k 行前端
// 从未跑过 lint，全量风格/类型规则会用成百上千条存量告警淹没真正要抓的 hooks 违规。
// 只借 @typescript-eslint/parser 把 TSX 语法解析对，规则集保持最小。
import tsParser from '@typescript-eslint/parser'
import reactHooks from 'eslint-plugin-react-hooks'

export default [
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      'react-hooks/rules-of-hooks': 'error', // 崩页类，必须挡 CI
      'react-hooks/exhaustive-deps': 'warn',  // 依赖遗漏多为隐患非崩溃，先 warn 不阻断
    },
  },
]
