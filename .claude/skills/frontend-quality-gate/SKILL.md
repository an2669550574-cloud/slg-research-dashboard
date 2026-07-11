---
name: frontend-quality-gate
description: 前端/接口改动合并前的「质量门」——性能预算(Core Web Vitals / bundle / N+1)、可访问性(键盘 / ARIA / 焦点)、loading-error-empty 三态、devtools 验证(console 干净 / 网络码 / 前后截图)。Use when building or reviewing dashboard 前端组件/页面 or 列表/详情类 API endpoint, 合并前过一遍。与 frontend-design 正交:那个管「好不好看」,这个管「对不对、快不快、能不能用」。吸收自 addyosmani/agent-skills 的 performance-optimization / frontend-ui-engineering / browser-testing-with-devtools,已按本项目栈(React18+Vite+TanStack Query+Recharts+Tailwind / FastAPI async SQLAlchemy)裁剪。
---

本项目的前端工程质量门。**不是**美学 skill(那是 `frontend-design`),也**不是**业务情报价值优化(那是 `docs/OPTIMIZATION-2026-07.md`)——这里只管三件事:**快、可访问、验证过**。

触发:新增/改动前端组件或页面、新增列表/详情类 API endpoint、或对上述做 review 时。琐碎文案/样式微调不必全套。

## 铁律:先量后改(measure-first)

性能改动没有前后数字 = 瞎猜。流程 **MEASURE → IDENTIFY → FIX → VERIFY → GUARD**:先量基线拿真实数字 → 定位真瓶颈(别假设)→ 只改那一处 → 再量确认 → 加测试/监控防回退。这与本项目「最糟样本铁律」一脉相承:优化也要拿已知案例回放核对。

## 1. 性能预算(本栈高危点已标注)

目标线:LCP ≤ 2.5s · INP ≤ 200ms · CLS ≤ 0.1 · 初始 JS bundle < 200KB gzip · API p95 < 200ms。

- **Recharts 是本项目最重的前端依赖**——收入/下载/排名趋势图、对比页叠加曲线都靠它。检查:① 图表组件是否 `React.lazy` / 路由级 code-split,别让详情页的图表把首屏 bundle 撑爆;② `npm run build` 后看 chunk 体积,Recharts 单独成 chunk。
- **N+1 是 async SQLAlchemy 的头号坑**。榜单/新品/厂商这类一对多读取,确认用 `selectinload`/`joinedload` 一次拉关联,而不是循环里每行一条 query。新写的读路径尤其查。
- **列表 endpoint 必须分页**(显式 `limit`/`offset` 或游标),别无界返回。
- **无谓 re-render**:传给 Recharts/大列表的 `data`/`options` 对象引用要稳(`useMemo`),回调 `useCallback`;昂贵纯组件才上 `React.memo`——**别到处 memo**(过度 memo 本身是 red flag)。
- **缓存**:静态资源走 Vite 内容哈希 + `immutable` 长缓存;ST 这类配额受限数据后端已有缓存,前端 TanStack Query 的 `staleTime` 按数据新鲜度设,别每次挂载都打后端。

## 2. 可访问性(键盘可用 + 屏读可懂)

- **图标按钮必须有可访问名**。本项目大量用 `lucide-react` 做纯图标按钮(关闭/筛选/展开),这些**必须** `aria-label`,否则屏读只念「button」。`<button aria-label="转入深度追踪"><Star/></button>`。
- **抽屉/弹窗要管焦点**:新品抽屉(`NewReleases.tsx`)、对比选择等打开时把焦点移进去、`Esc` 关闭、焦点 trap 在内部,关闭后焦点还给触发元素(`useRef`+`useEffect`)。
- **别只靠颜色传状态**。走势 chip(检出→现名次 ↗ / 已掉榜 ✝)已经是「文字/图标 + 颜色」双编码,保持这个习惯;新增红绿升降同理带箭头/文字。
- 对比度 ≥ 4.5:1(正文)/ 3:1(大字);标题层级不跳级(h1→h2→h3);可 Tab 遍历、焦点顺序合理。

## 3. loading / error / empty 三态(TanStack Query 场景必查)

看板全是异步数据,每个数据视图三态齐全:

- **loading**:骨架屏优先于转圈(`animate-pulse` 占位块),容器 `aria-busy="true"` + 描述性 label。
- **error**:明确错误信息 + 重试入口(别白屏/静默)。
- **empty**:文案 + 下一步动作(如「本窗口无新品,试试放宽天数」),别给空白网格。

写入类操作可用乐观更新(React Query `onMutate` 立即改 UI,`onError` 回滚),如「一键晋升 tracked」。

## 4. 状态归属:筛选/分页优先放 URL

国家/平台/关键字/天数这些筛选、分页,优先用 `searchParams`(URL state)而非组件内 `useState`——**可分享、可刷新保留、可深链**(正好对齐项目「深链」诉求)。层级从简到繁:local → lifted →(URL)→ server(React Query)→ 才轮到全局 store。prop 别钻超过 3 层。

## 5. devtools 验证门(补 `verify` skill / HK 预览之后)

现有流程(vitest / pytest / 「HK 代理预览后截图」)之上,合并前在真实浏览器补这几关:

- **console 必须干净**:零 error、零 warning(含 React key / deprecation / a11y 警告)。「已知问题」不算豁免。
- **Network**:相关请求返回预期状态码与数据体;4xx=前端传参错、5xx=后端、CORS=头不匹配。
- **前后截图对比**:UI 改动必须在浏览器里看过并留 before/after 截图(本地 mock 数据不足以显示走势 chip 等,按项目既有「部署后 HK 预览截图」补)。
- **a11y 树**:抽查交互元素有无可访问名、结构是否合理。

## 合并前 checklist

- [ ] 性能改动有前后数字;新读路径无 N+1;列表有分页
- [ ] Recharts/图表已 code-split,`npm run build` chunk 体积没异常增长
- [ ] 图标按钮都有 `aria-label`;抽屉/弹窗焦点管理正确;状态非纯颜色编码
- [ ] 每个数据视图 loading/error/empty 三态齐全
- [ ] 筛选/分页尽量走 URL state
- [ ] 浏览器验证:console 干净、网络码正确、before/after 截图、a11y 树抽查
- [ ] `cd frontend && npm run build && npm run test` + `cd backend && pytest` 全绿

## Red flags(见到即停)

无 profiling 数据就优化 · N+1 · 列表无分页 · 图片无尺寸/懒加载 · bundle 悄悄变大没人看 · 到处 `React.memo`/`useMemo` · 图标按钮无 label · 忽略 console 报错 · 改了 UI 没在浏览器里看过就合。
