import { useEffect, useState, useMemo, useRef } from 'react'
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { materialsApi, gamesApi } from '../lib/api'
import { PLATFORM_CONFIG } from '../lib/utils'
import { ExternalLink, Trash2, Plus, Search, Download as DownloadIcon, Upload, Film as FilmIcon, Radio, Pencil, X, Check, AlertCircle, Loader2, Tag as TagIcon, Sparkles, SlidersHorizontal, Wand2, MessagesSquare, ChevronDown, ChevronRight, Layers } from 'lucide-react'
import { MaterialPreview } from '../components/MaterialPreview'
import { MaterialAnalysisDrawer } from '../components/MaterialAnalysisDrawer'
import {
  StructuredTagEditor, emptyTagState, tagStateFromItems, tagStateToInputs, missingRequiredNames,
  type TagValueState,
} from '../components/StructuredTagEditor'
import { tagsApi } from '../lib/api'
import { composeNameFromTags, composeNameFromTagValues, parseTagsFromName, mergeTagStates } from '../lib/tagName'
import { TagAggregatePanel } from '../components/TagAggregatePanel'
import { TagAnalysisAgent } from '../components/TagAnalysisAgent'
import { Select } from '../components/Select'
import { PageHeader } from '../components/PageHeader'
import { useNavigate } from 'react-router-dom'
import { downloadCsv } from '../lib/csv'
import { useT } from '../i18n'
import { Pagination } from '../components/Pagination'
import { QueryError } from '../components/QueryError'
import { useDebouncedValue, useLocalStorageState } from '../lib/hooks'
import type { MaterialOut, TagDimension } from '../lib/types'

const PAGE_SIZE = 12
const MAX_UPLOAD = 200 * 1024 * 1024
const ACCEPT = '.mp4,.webm,.mov,.m4v,.jpg,.jpeg,.png,.gif,.webp'
const IMG_EXT = /\.(jpe?g|png|gif|webp)$/i
// 后端按扩展名判 kind 并对 material_type 不符回 400；前端用同一套规则推断，避免误报。
const inferType = (name: string) => (IMG_EXT.test(name) ? 'image' : 'video')
const stem = (name: string) => name.replace(/\.[^.]+$/, '')

/** 按标签重命名的计划项（预览 + 执行 + 撤销共用同一份，保证「看到的=改成的=能还原的」）。 */
type RenamePlanItem = { id: number; oldTitle: string; newTitle: string }
/** 从本页素材算重命名计划：撞名补 -2/-3 后缀去重、跳过标题无变化的。预览与批量执行共用此函数。 */
function computeRenamePlan(mats: MaterialOut[]): RenamePlanItem[] {
  const seen = new Map<string, number>()
  const plan: RenamePlanItem[] = []
  for (const m of mats) {
    const base = composeNameFromTagValues(m.tag_values ?? [])
    if (!base) continue
    const dup = seen.get(base) ?? 0
    seen.set(base, dup + 1)
    const newTitle = dup === 0 ? base : `${base}-${dup + 1}`
    if (newTitle === m.title) continue  // 已是目标名，不算入计划
    plan.push({ id: m.id, oldTitle: m.title, newTitle })
  }
  return plan
}

/** 分面栏单个维度行：维度名 + 一排可点二级标签 chip（通用组/产品组共用）。 */
function FacetRow({ d, filterOptions, onToggle }: {
  d: TagDimension; filterOptions: Set<number>; onToggle: (id: number) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] text-secondary min-w-[52px]">{d.name}</span>
      {d.options.map(o => {
        const active = filterOptions.has(o.id)
        return (
          <button key={o.id} onClick={() => onToggle(o.id)} title={o.value}
            className={`px-2.5 py-0.5 rounded-md text-xs border transition-colors ${active ? 'bg-accent/15 border-accent/40 text-accent' : 'border-default text-secondary hover:border-strong hover:text-primary'}`}>
            {o.value}
          </button>
        )
      })}
    </div>
  )
}

const inputClass =
  "w-full bg-elevated/60 border border-default rounded-lg px-3 py-2.5 text-sm text-primary placeholder:text-muted focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 transition-colors"

type QStatus = 'pending' | 'uploading' | 'done' | 'error'
interface QItem { name: string; status: QStatus; pct: number; error?: string }

const emptyForm = { title: '', url: '', app_id: '', platform: 'other', material_type: 'video', tags: '', notes: '' }

export default function Materials() {
  const navigate = useNavigate()
  const t = useT()
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [filterPlatform, setFilterPlatform] = useState('')
  const [filterType, setFilterType] = useState('')
  const [filterGame, setFilterGame] = useState('')
  const [filterTag, setFilterTag] = useState('')
  const [filterOptions, setFilterOptions] = useState<Set<number>>(new Set())
  const [sort, setSort] = useState('created_at:desc')
  const [offset, setOffset] = useState(0)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState(emptyForm)
  // 默认「上传文件」：团队实际几乎只用上传，外链既少用也拉不回视频文件，故排后
  const [mode, setMode] = useState<'link' | 'upload'>('upload')
  const [editing, setEditing] = useState<MaterialOut | null>(null)
  const [tagValues, setTagValues] = useState<TagValueState>(emptyTagState())
  const [analyzing, setAnalyzing] = useState<MaterialOut | null>(null)
  const [agentOpen, setAgentOpen] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const [parseOn, setParseOn] = useState(true) // 文件名反解打标（P0）：默认开
  const [queue, setQueue] = useState<QItem[]>([])
  const [busy, setBusy] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement | null>(null)
  const debouncedSearch = useDebouncedValue(search)

  const [sortBy, order] = sort.split(':') as ['created_at' | 'title', 'asc' | 'desc']

  // 分面筛选（P3）：选中的二级标签 id 排序后逗号拼成稳定 key，既当 queryKey 又当请求参数。
  const facetKey = useMemo(() => [...filterOptions].sort((a, b) => a - b).join(','), [filterOptions])

  // ── 标签包（切片 2）：选了具体游戏且该产品开关开启时，分面区顶部出现包切换 chips ──
  // 包只是**分面视图**的收窄（选包 → 只展示成员维度的分面行），不直接过滤素材；
  // 「仅看已打标」勾选后才叠加 has_dimensions 过滤。开关无记录 = 默认关，界面与现状一致。
  const { data: packSetting } = useQuery({
    queryKey: ['tagPackSetting', filterGame],
    queryFn: () => tagsApi.getPackSetting(filterGame),
    enabled: !!filterGame,
  })
  const { data: packs = [] } = useQuery({
    queryKey: ['tagPacks', filterGame],
    queryFn: () => tagsApi.listPacks(filterGame),
    enabled: !!filterGame,
  })
  const packsEnabled = !!filterGame && !!packSetting?.enabled
  const [activePackId, setActivePackId] = useLocalStorageState<number | null>('mat.activePack', null)
  const [packTaggedOnly, setPackTaggedOnly] = useLocalStorageState<boolean>('mat.packTaggedOnly', false)
  // 被删的包 / 换游戏后不可见的包自动回落「全部」（find 不到 = null）
  const currentPack = packsEnabled ? (packs.find(p => p.id === activePackId) ?? null) : null
  const packSettingMut = useMutation({
    mutationFn: (enabled: boolean) => tagsApi.putPackSetting(filterGame, enabled),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['tagPackSetting', filterGame] })
      toast.success(res.enabled ? t.materials.packEnabledToast : t.materials.packDisabledToast)
    },
  })
  // 「仅看已打标」请求参数：当前包成员维度 id 排序拼接（稳定 key，兼当 queryKey）
  const hasDimsKey = currentPack && packTaggedOnly
    ? [...currentPack.dimension_ids].sort((a, b) => a - b).join(',')
    : ''

  useEffect(() => { setOffset(0) }, [debouncedSearch, filterPlatform, filterType, filterGame, filterTag, facetKey, hasDimsKey, sort])

  const { data: paged, isLoading, isError, refetch } = useQuery({
    queryKey: ['materials', debouncedSearch, filterPlatform, filterType, filterGame, filterTag, facetKey, hasDimsKey, sort, offset],
    queryFn: () => materialsApi.listPaged({
      limit: PAGE_SIZE, offset,
      q: debouncedSearch || undefined,
      platform: filterPlatform || undefined,
      material_type: filterType || undefined,
      app_id: filterGame || undefined,
      tag: filterTag || undefined,
      tag_options: facetKey || undefined,
      has_dimensions: hasDimsKey || undefined,
      sort_by: sortBy, order,
    }),
    placeholderData: keepPreviousData,
    // 有素材处于 running 时轮询，让列表徽标在后台分析完成后自动翻 done/failed（详情抽屉只轮询单条）
    refetchInterval: (query) =>
      query.state.data?.items.some(m => m.analysis_status === 'running') ? 4000 : false,
  })
  const materials: MaterialOut[] = paged?.items ?? []
  const total = paged?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const page = Math.floor(offset / PAGE_SIZE) + 1

  // 后台分析完成提示：对比上一帧各素材状态，捕捉 running→done / running→failed 的跃迁。
  // 翻页换来的新条目其「上一帧」为空，不会误报；轮询刷新同页时只在状态真变化时弹一次。
  const prevStatusRef = useRef<Record<number, string>>({})
  useEffect(() => {
    const prev = prevStatusRef.current
    let done = 0
    let failed = 0
    for (const m of materials) {
      if (prev[m.id] === 'running' && m.analysis_status === 'done') done++
      if (prev[m.id] === 'running' && m.analysis_status === 'failed') failed++
    }
    if (done > 0) toast.success(t.materials.analyzeDone(done))
    if (failed > 0) toast.error(t.materials.analyzeFailed(failed))
    const next: Record<number, string> = {}
    for (const m of materials) if (m.analysis_status) next[m.id] = m.analysis_status
    prevStatusRef.current = next
  }, [materials, t])

  const { data: allGames = [] } = useQuery({
    queryKey: ['games', 'tracked'],
    queryFn: () => gamesApi.list({ limit: 200 }),
  })

  // 标签栏跟随"按游戏筛选"联动：选了某游戏只列该游戏的标签。零 ST 配额。
  const { data: tagCounts = [] } = useQuery({
    queryKey: ['materialTags', filterGame],
    queryFn: () => materialsApi.tags(filterGame || undefined),
  })

  // 分面筛选维度（P3 + S3）：跟随类型 + 当前游戏筛选取适用的一级标签；只用文字型(有二级选项)做分面。
  // 选了游戏时按产品作用域收敛：只显示该游戏可见的维度/选项，与打标签编辑器口径一致。
  // 与编辑器/表单共享 ['tagDimensions', type, appId] 缓存。零 ST 配额。
  const { data: facetDims = [] } = useQuery({
    queryKey: ['tagDimensions', filterType || 'all', filterGame || 'any'],
    queryFn: () => tagsApi.listDimensions(filterType || undefined, filterGame || undefined),
  })
  const facetable = facetDims.filter(d => d.value_type === 'text' && d.options.length > 0)
  const toggleFacet = (optId: number) => setFilterOptions(prev => {
    const next = new Set(prev)
    next.has(optId) ? next.delete(optId) : next.add(optId)
    return next
  })
  // 选包时分面只剩成员维度；其余进「未分组」折叠段（默认收起，防"进包视图就找不到维度"）
  const packDimIds = currentPack ? new Set(currentPack.dimension_ids) : null
  const effectiveFacetable = packDimIds ? facetable.filter(d => packDimIds.has(d.id)) : facetable
  const ungroupedFacetable = packDimIds ? facetable.filter(d => !packDimIds.has(d.id)) : []
  const ungroupedActiveCount = ungroupedFacetable.reduce(
    (n, d) => n + d.options.filter(o => filterOptions.has(o.id)).length, 0)

  // 分面按产品作用域分组（治「维度多铺满屏」）：通用维度常驻，各产品专属维度折进可展开的组。
  // 组 key = 单产品 app_id / 多产品维度归 '__multi__'。选了游戏时 facetable 已收敛，分组自然只剩少量。
  const gameNameMap = useMemo(() => Object.fromEntries(allGames.map(g => [g.app_id, g.name])), [allGames])
  const facetGroups = useMemo(() => {
    const universal = effectiveFacetable.filter(d => !(d.app_ids?.length))
    const byKey = new Map<string, { key: string; label: string; dims: TagDimension[] }>()
    for (const d of effectiveFacetable) {
      const ids = d.app_ids ?? []
      if (ids.length === 0) continue
      const key = ids.length === 1 ? ids[0] : '__multi__'
      const label = ids.length === 1 ? (gameNameMap[ids[0]] || ids[0]) : t.materials.facetGroupMulti
      if (!byKey.has(key)) byKey.set(key, { key, label, dims: [] })
      byKey.get(key)!.dims.push(d)
    }
    // 含激活筛选的组要能一眼看到有没有选中：算每组已选数
    const groups = [...byKey.values()].map(g => ({
      ...g, activeCount: g.dims.reduce((n, d) => n + d.options.filter(o => filterOptions.has(o.id)).length, 0),
    }))
    groups.sort((a, b) => a.label.localeCompare(b.label))
    return { universal, groups }
  }, [effectiveFacetable, filterOptions, gameNameMap, t])
  // 展开的产品组（localStorage 记忆）；产品组默认折叠。含已选中项的组自动视作展开。
  const [expandedFacetGroups, setExpandedFacetGroups] = useLocalStorageState<string[]>('mat.facetGroups', [])
  const toggleFacetGroup = (key: string) => setExpandedFacetGroups(
    expandedFacetGroups.includes(key) ? expandedFacetGroups.filter(k => k !== key) : [...expandedFacetGroups, key])

  // 结构化标签编辑器跟随的素材类型：上传按首个文件推断，其余看表单选择。
  const editorMaterialType = mode === 'upload' && !editing
    ? (files[0] ? inferType(files[0].name) : 'video')
    : form.material_type
  // 与 StructuredTagEditor 内部同一 3 段 queryKey（type + appId）真正共享缓存；
  // 补上产品作用域后，必填校验与文件名反解都只看该产品可见的维度。
  const { data: editorDims = [] } = useQuery({
    queryKey: ['tagDimensions', editorMaterialType || 'all', form.app_id || 'any'],
    queryFn: () => tagsApi.listDimensions(editorMaterialType || undefined, form.app_id || undefined),
    enabled: showForm,
  })
  // 文件名反解（P0）：选完文件即逐文件解析，预填结构化标签；提交时解析维度优先、
  // 共享编辑器补缺（mergeTagStates）。纯前端确定性词表匹配，零 LLM。
  const parsedByFile = useMemo(
    () => (mode === 'upload' && !editing && parseOn && editorDims.length
      ? files.map(f => parseTagsFromName(stem(f.name), editorDims))
      : []),
    [files, editorDims, parseOn, mode, editing],
  )
  const dimNameById = useMemo(() => new Map(editorDims.map(d => [d.id, d.name])), [editorDims])
  const optValueById = useMemo(
    () => new Map(editorDims.flatMap(d => d.options.map(o => [o.id, o.value] as const))),
    [editorDims],
  )
  const parseUnmatchedFiles = parsedByFile.filter(p => p.unmatched.length > 0).length
  // 自动命名（P5）：从当前已选结构化标签按维度顺序拼出标题（模板化，零 LLM/配额）。
  const autoNameSuggestion = useMemo(
    () => composeNameFromTags(tagValues, editorDims),
    [tagValues, editorDims],
  )

  const closeForm = () => {
    setShowForm(false); setEditing(null)
    setForm(emptyForm); setMode('upload'); setFiles([]); setQueue([])
    setTagValues(emptyTagState())
    if (fileInputRef.current) fileInputRef.current.value = ''
    if (folderInputRef.current) folderInputRef.current.value = ''
    setDragActive(false)
  }
  const afterMutate = (msg: string) => {
    closeForm()
    qc.invalidateQueries({ queryKey: ['materials'] })
    qc.invalidateQueries({ queryKey: ['materialTags'] })
    toast.success(msg)
  }

  // 后端校验失败（如必填标签缺失）时取 detail 展示，否则回落到通用文案。
  const errDetail = (e: any, fallback: string) => e?.response?.data?.detail || fallback

  const createMut = useMutation({
    mutationFn: (data: any) => materialsApi.create(data),
    onSuccess: () => afterMutate(t.materials.addedToast),
    onError: (e: any) => toast.error(errDetail(e, t.materials.saveFailed)),
  })
  const updateMut = useMutation({
    // 先存素材字段，再整体替换结构化标签（必填校验在后端）
    mutationFn: async ({ id, data, tagInputs }: { id: number; data: any; tagInputs: any[] }) => {
      await materialsApi.update(id, data)
      return materialsApi.setTagValues(id, tagInputs)
    },
    onSuccess: () => afterMutate(t.materials.savedToast),
    onError: (e: any) => toast.error(errDetail(e, t.materials.saveFailed)),
  })
  const deleteMut = useMutation({
    mutationFn: (id: number) => materialsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['materials'] })
      qc.invalidateQueries({ queryKey: ['materialTags'] })
      toast.success(t.materials.deletedToast)
    },
  })

  // 按结构化标签命名：直接用素材自身 tag_values 拼标题（零 LLM/配额）。
  // 卡片单条即时改；批量对「本页」已打标签的素材串行改，名字撞了补 -2/-3 后缀去重。
  // 撤销 toast：重命名不可逆（旧标题被覆盖），完成后给 6 秒撤销窗口（点了才还原）。
  const showUndoToast = (msg: string, undo: () => void) =>
    toast((tst) => (
      <span className="flex items-center gap-3">
        <span>{msg}</span>
        <button onClick={() => { toast.dismiss(tst.id); undo() }}
          className="shrink-0 text-accent font-medium hover:underline">{t.common.undo}</button>
      </span>
    ), { duration: 6000 })

  // 执行 / 撤销共用：逐条 PUT，返回真正改动了的项（供撤销）。单条失败跳过不拖累其余。
  const applyRename = async (plan: RenamePlanItem[]) => {
    const done: RenamePlanItem[] = []
    for (const it of plan) {
      try { await materialsApi.update(it.id, { title: it.newTitle }); done.push(it) } catch { /* skip */ }
    }
    qc.invalidateQueries({ queryKey: ['materials'] })
    return done
  }
  const undoRename = async (items: RenamePlanItem[]) => {
    for (const it of items) {
      try { await materialsApi.update(it.id, { title: it.oldTitle }) } catch { /* skip */ }
    }
    qc.invalidateQueries({ queryKey: ['materials'] })
    toast.success(t.materials.renameUndone(items.length))
  }

  const renameOneByTags = async (m: MaterialOut) => {
    const name = composeNameFromTagValues(m.tag_values ?? [])
    if (!name) { toast(t.materials.nameByTagsNoTags); return }
    if (name === m.title) { toast(t.materials.nameByTagsSame); return }
    const item: RenamePlanItem = { id: m.id, oldTitle: m.title, newTitle: name }
    await applyRename([item])
    showUndoToast(t.materials.nameByTagsDone, () => undoRename([item]))
  }
  // 批量：点按钮先算计划、开预览 modal（列旧名→新名），确认后才执行 + 给撤销。
  const [renamePlan, setRenamePlan] = useState<RenamePlanItem[] | null>(null)
  const [renaming, setRenaming] = useState(false)
  const openRenameBatch = () => {
    const plan = computeRenamePlan(materials)
    if (plan.length === 0) { toast(t.materials.nameByTagsBatchNone); return }
    setRenamePlan(plan)
  }
  const confirmRenameBatch = async () => {
    if (!renamePlan) return
    setRenaming(true)
    const done = await applyRename(renamePlan)
    setRenaming(false)
    setRenamePlan(null)
    if (done.length > 0) showUndoToast(t.materials.nameByTagsBatchResult(done.length), () => undoRename(done))
  }

  // 按标题解析补标（P0 的镜像动作）：对「本页」零结构化标签且已归属游戏的素材，
  // 用文件名解析器从标题反解并写入。**只补空、绝不覆盖已打标签**（人工标签是权威）；
  // 维度按 (素材类型, app_id) 取作用域名单，跨调用缓存避免重复请求。
  const fillTagsByTitleBatch = async () => {
    const candidates = materials.filter(m => m.app_id && !(m.tag_values ?? []).length)
    if (candidates.length === 0) { toast(t.materials.fillByTitleNone); return }
    const dimsCache = new Map<string, TagDimension[]>()
    const dimsFor = async (m: MaterialOut) => {
      const key = `${m.material_type}|${m.app_id}`
      if (!dimsCache.has(key)) {
        dimsCache.set(key, await tagsApi.listDimensions(m.material_type || undefined, m.app_id))
      }
      return dimsCache.get(key)!
    }
    // 先全部试解析拿到真实可补数，再让用户确认
    const jobs: { m: MaterialOut; inputs: ReturnType<typeof tagStateToInputs> }[] = []
    for (const m of candidates) {
      const dims = await dimsFor(m)
      if (!dims.length) continue
      const { state } = parseTagsFromName(m.title, dims)
      const inputs = tagStateToInputs(state)
      if (inputs.length) jobs.push({ m, inputs })
    }
    if (jobs.length === 0) { toast(t.materials.fillByTitleNone); return }
    if (!window.confirm(t.materials.fillByTitleConfirm(jobs.length))) return
    let done = 0
    for (const { m, inputs } of jobs) {
      try { await materialsApi.setTagValues(m.id, inputs); done++ } catch { /* 单条失败跳过 */ }
    }
    qc.invalidateQueries({ queryKey: ['materials'] })
    qc.invalidateQueries({ queryKey: ['materialTags'] })
    toast.success(t.materials.fillByTitleResult(done))
  }

  const gameMap = useMemo(() => Object.fromEntries(allGames.map(g => [g.app_id, g])), [allGames])
  const typeLabel = (kind: string) => t.materials.types[kind as keyof typeof t.materials.types] || kind
  const platLabel = (p: string) =>
    t.materials.platforms[p as keyof typeof t.materials.platforms] || PLATFORM_CONFIG[p]?.label || p

  const openEdit = (m: MaterialOut) => {
    setEditing(m)
    setMode(m.source === 'upload' ? 'upload' : 'link')
    setForm({
      title: m.title, url: m.url ?? '', app_id: m.app_id,
      platform: m.platform ?? 'other', material_type: m.material_type,
      tags: m.tags.join(', '), notes: m.notes ?? '',
    })
    setTagValues(tagStateFromItems(m.tag_values))
    setFiles([]); setQueue([]); setShowForm(true)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  // 接受一批 (file, relativePath)：按扩展名过滤 + 大小校验 + 顶层文件夹
  // 名自动并入标签。文件夹选择/拖入文件夹/普通多选三条路径共用此函数。
  const acceptItems = (items: { file: File; path: string }[]) => {
    if (items.length === 0) return
    const total = items.length
    const supported = items.filter(({ file }) =>
      ACCEPT.split(',').some(ext => file.name.toLowerCase().endsWith(ext)))
    const tooBig = supported.find(({ file }) => file.size > MAX_UPLOAD)
    if (tooBig) { toast.error(t.materials.fileTooLarge(200)); return }
    if (supported.length === 0) {
      toast.error(t.materials.skippedNonMedia(total))
      return
    }
    // 顶层文件夹名 → 自动标签（合并进 form.tags，保留用户已输入的）
    const folderTags = Array.from(new Set(
      supported.map(({ path }) => path.includes('/') ? path.split('/')[0] : '')
               .filter(Boolean)))
    if (folderTags.length > 0) {
      setForm(s => {
        const existing = s.tags ? s.tags.split(',').map(x => x.trim()).filter(Boolean) : []
        const merged = Array.from(new Set([...existing, ...folderTags]))
        return { ...s, tags: merged.join(', ') }
      })
    }
    setFiles(supported.map(s => s.file))
    if (supported.length === 1 && !form.title) {
      setForm(s => ({ ...s, title: stem(supported[0].file.name) }))
    }
    const skipped = total - supported.length
    if (skipped > 0) toast(t.materials.skippedNonMedia(skipped))
  }

  // 拖拽：递归遍历文件夹（webkitGetAsEntry / FileSystemEntry,Chrome/Edge/
  // Firefox/Safari 现代版均支持）。createReader().readEntries 一次最多 100,
  // 须循环读到空。
  const readDirEntries = (dir: FileSystemDirectoryEntry): Promise<FileSystemEntry[]> => {
    const reader = dir.createReader()
    const all: FileSystemEntry[] = []
    return new Promise((resolve, reject) => {
      const pump = () => reader.readEntries(batch => {
        if (batch.length === 0) resolve(all)
        else { all.push(...batch); pump() }
      }, reject)
      pump()
    })
  }
  const traverseEntry = async (entry: FileSystemEntry, prefix: string): Promise<{file: File; path: string}[]> => {
    if (entry.isFile) {
      const file = await new Promise<File>((res, rej) =>
        (entry as FileSystemFileEntry).file(res, rej))
      return [{ file, path: prefix + file.name }]
    }
    if (entry.isDirectory) {
      const entries = await readDirEntries(entry as FileSystemDirectoryEntry)
      const nested = await Promise.all(
        entries.map(e => traverseEntry(e, prefix + entry.name + '/')))
      return nested.flat()
    }
    return []
  }
  const onDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault(); setDragActive(false)
    const dtItems = Array.from(e.dataTransfer.items ?? [])
    const collected: {file: File; path: string}[] = []
    for (const it of dtItems) {
      const entry = (it as any).webkitGetAsEntry?.() as FileSystemEntry | null
      if (entry) {
        try { collected.push(...await traverseEntry(entry, '')) } catch { /* 忽略单条 */ }
      } else {
        const f = it.getAsFile?.()
        if (f) collected.push({ file: f, path: '' })
      }
    }
    acceptItems(collected)
  }

  // 批量上传：前端串行调用现有 /upload。每个文件独立进度/成败，
  // 部分失败不影响其余；大文件(≤200MB)逐个走，比单请求收一堆稳。
  const runBatch = async () => {
    const tags = form.tags ? form.tags.split(',').map(s => s.trim()).filter(Boolean) : []
    // 逐文件标签：文件名解析出的维度优先，共享编辑器只补未解析出的维度
    const mergedFor = (i: number) =>
      parseOn && parsedByFile[i] ? mergeTagStates(tagValues, parsedByFile[i].state) : tagValues
    // 必填预检（合并后口径）：任一文件缺必填就整批不发，避免中途碎败
    const problems = files
      .map((f, i) => ({ name: stem(f.name), missing: missingRequiredNames(editorDims, mergedFor(i)) }))
      .filter(p => p.missing.length)
    if (problems.length) {
      toast.error(t.materials.parseRequiredMissing(problems[0].name, problems[0].missing.join('、'), problems.length))
      return
    }
    const list = files
    setQueue(list.map(f => ({ name: f.name, status: 'pending', pct: 0 })))
    setBusy(true)
    let ok = 0
    for (let i = 0; i < list.length; i++) {
      const f = list[i]
      setQueue(q => q.map((it, idx) => idx === i ? { ...it, status: 'uploading' } : it))
      const fd = new FormData()
      fd.append('file', f)
      fd.append('title', list.length === 1 && form.title ? form.title : stem(f.name))
      fd.append('app_id', form.app_id)
      fd.append('platform', form.platform)
      fd.append('material_type', inferType(f.name))
      fd.append('tags', tags.join(','))
      fd.append('tag_values', JSON.stringify(tagStateToInputs(mergedFor(i))))
      if (form.notes) fd.append('notes', form.notes)
      try {
        await materialsApi.upload(fd, pct =>
          setQueue(q => q.map((it, idx) => idx === i ? { ...it, pct } : it)))
        ok++
        setQueue(q => q.map((it, idx) => idx === i ? { ...it, status: 'done', pct: 100 } : it))
      } catch (e: any) {
        const msg = e?.response?.data?.detail || e?.message || 'error'
        setQueue(q => q.map((it, idx) => idx === i ? { ...it, status: 'error', error: String(msg) } : it))
      }
    }
    setBusy(false)
    qc.invalidateQueries({ queryKey: ['materials'] })
    qc.invalidateQueries({ queryKey: ['materialTags'] })
    const fail = list.length - ok
    if (fail === 0) { closeForm(); toast.success(t.materials.batchResult(ok, 0)) }
    else toast.error(t.materials.batchResult(ok, fail))
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const tags = form.tags ? form.tags.split(',').map(s => s.trim()).filter(Boolean) : []
    const tagInputs = tagStateToInputs(tagValues)
    // 提交前本地必填校验（后端仍是权威，这里只为少跑一次 400）。
    // 上传路径不在这查：文件名解析后各文件标签不同，runBatch 里按合并后口径逐文件预检。
    const isUploadBatch = mode === 'upload' && !editing
    if (!isUploadBatch) {
      const missing = missingRequiredNames(editorDims, tagValues)
      if (missing.length) { toast.error(t.materials.missingRequiredTags(missing.join('、'))); return }
    }

    if (editing) {
      const data: any = {
        title: form.title, app_id: form.app_id, platform: form.platform,
        material_type: form.material_type, tags, notes: form.notes,
      }
      if (editing.source === 'link') data.url = form.url
      updateMut.mutate({ id: editing.id, data, tagInputs })
      return
    }
    if (mode === 'link') { createMut.mutate({ ...form, tags, tag_values: tagInputs }); return }
    if (files.length === 0) { toast.error(t.materials.chooseFile); return }
    const tooBig = files.find(f => f.size > MAX_UPLOAD)
    if (tooBig) { toast.error(t.materials.fileTooLarge(200)); return }
    runBatch()
  }

  const exportCsv = async () => {
    // 导出整套匹配结果（不只当前页）。limit=200 是后端硬上限。
    const all = await materialsApi.listPaged({
      limit: 200, offset: 0,
      q: debouncedSearch || undefined,
      platform: filterPlatform || undefined,
      material_type: filterType || undefined,
      app_id: filterGame || undefined,
      tag: filterTag || undefined,
      tag_options: facetKey || undefined,
      sort_by: sortBy, order,
    }).catch(() => null)
    if (!all || all.items.length === 0) { toast.error(t.common.noExportData); return }
    const date = new Date().toISOString().slice(0, 10)
    downloadCsv(`materials-${date}.csv`, all.items, [
      { header: t.csv.game, get: (m: MaterialOut) => gameMap[m.app_id]?.name || m.app_id },
      { header: t.csv.title, get: (m: MaterialOut) => m.title },
      { header: t.csv.platform, get: (m: MaterialOut) => m.platform ?? '' },
      { header: t.csv.type, get: (m: MaterialOut) => m.material_type },
      { header: t.csv.url, get: (m: MaterialOut) => m.url ?? m.file_name ?? '' },
      { header: t.csv.tags, get: (m: MaterialOut) => m.tags.join(';') },
      { header: t.csv.notes, get: (m: MaterialOut) => m.notes ?? '' },
      { header: t.csv.createdAt, get: (m: MaterialOut) => m.created_at },
    ])
    toast.success(t.common.exported(all.items.length))
  }

  const PLATFORM_TABS = ['', 'youtube', 'tiktok', 'meta', 'other']
  const sortOptions = [
    { value: 'created_at:desc', label: t.materials.sortNewest },
    { value: 'created_at:asc', label: t.materials.sortOldest },
    { value: 'title:asc', label: t.materials.sortTitleAz },
    { value: 'title:desc', label: t.materials.sortTitleZa },
  ]

  const AssetCard = ({ m, n }: { m: MaterialOut; n: number }) => {
    const platCfg = (m.platform && PLATFORM_CONFIG[m.platform]) || PLATFORM_CONFIG.other
    const game = gameMap[m.app_id]
    const href = (m.source === 'upload' ? m.stream_url : m.url) as string | undefined
    const hasPreview = m.source === 'upload' && !!m.stream_url
    const media = (
      <div className="hud relative aspect-video w-full bg-gradient-to-br from-elevated to-base overflow-hidden">
        {hasPreview ? <MaterialPreview m={m} fill /> : (
          <div className="absolute inset-0 grid place-items-center text-muted/40">
            <FilmIcon size={26} />
          </div>
        )}
        <span className="absolute top-3 left-3 text-[11px] px-2 py-0.5 rounded bg-base/75 backdrop-blur-sm text-secondary border border-default">
          {m.platform ? platLabel(m.platform) : platCfg.label}
        </span>
        <div className="absolute top-3 right-3 flex gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
          {/* AI 分析：仅 upload 视频可用（外链拿不到原文件抽帧） */}
          {m.source === 'upload' && m.material_type === 'video' && (
            <button onClick={() => setAnalyzing(m)} title="AI 分析"
              className="p-1.5 rounded bg-base/75 backdrop-blur-sm text-secondary hover:text-accent">
              <Sparkles size={14} />
            </button>
          )}
          {m.tag_values?.length > 0 && (
            <button onClick={() => renameOneByTags(m)} title={t.materials.nameByTags}
              className="p-1.5 rounded bg-base/75 backdrop-blur-sm text-secondary hover:text-accent">
              <Wand2 size={14} />
            </button>
          )}
          <button onClick={() => openEdit(m)} title={t.materials.editMaterial}
            className="p-1.5 rounded bg-base/75 backdrop-blur-sm text-secondary hover:text-accent">
            <Pencil size={14} />
          </button>
          {href && (
            <a href={href} target="_blank" rel="noopener noreferrer" title={t.materials.openFile}
              className="p-1.5 rounded bg-base/75 backdrop-blur-sm text-secondary hover:text-accent">
              <ExternalLink size={14} />
            </a>
          )}
        </div>
        {/* 分析状态徽标：常驻显示（与平台徽标同侧底部），让列表一眼看出已分析的素材。
            失败态做成可点按钮：title 展示错误原因，点击打开抽屉重试 */}
        {m.source === 'upload' && m.material_type === 'video' && m.analysis_status && m.analysis_status !== 'pending' && (
          m.analysis_status === 'failed' ? (
            <button onClick={() => setAnalyzing(m)}
              title={m.analysis_error ? `${m.analysis_error}\n${t.materials.analyzeRetry}` : t.materials.analyzeRetry}
              className="absolute bottom-3 left-3 inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-data backdrop-blur-sm border bg-red-500/15 border-red-500/40 text-red-300 hover:bg-red-500/25 transition-colors">
              <AlertCircle size={9} /> {t.materials.analyzeFailedBadge}
            </button>
          ) : (
            <span className={`absolute bottom-3 left-3 inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-data backdrop-blur-sm border ${
              m.analysis_status === 'done' ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300'
              : 'bg-accent/15 border-accent/40 text-accent'
            }`}>
              {m.analysis_status === 'done' && <><Sparkles size={9} /> AI</>}
              {m.analysis_status === 'running' && <><Loader2 size={9} className="animate-spin" /> {t.materials.analyzeRunningBadge}</>}
            </span>
          )
        )}
      </div>
    )
    const meta = (
      <div className="flex flex-col gap-2 p-4">
        <div className="flex items-center gap-2 text-[11px] text-muted">
          <span className="font-data text-accent">{String(n).padStart(2, '0')}</span>
          <span className="text-muted/40">·</span>
          <span>{typeLabel(m.material_type)}</span>
          {game && (
            <>
              <span className="text-muted/40">·</span>
              <button onClick={() => navigate(`/game/${m.app_id}`)}
                className="text-accent hover:underline truncate max-w-[150px]">
                {game.name}
              </button>
            </>
          )}
        </div>
        <div className="font-display font-bold text-primary text-[15px] leading-tight line-clamp-2">
          {m.title}
        </div>
        {m.notes && <div className="text-xs text-muted line-clamp-1">{m.notes}</div>}
        {m.tags?.length > 0 && (
          <div className="flex gap-1.5 flex-wrap pt-0.5">
            {m.tags.map((tag: string) => (
              <button key={tag} onClick={() => setFilterTag(tag)} title={tag}
                className={`px-2 py-0.5 rounded border text-[11px] transition-colors ${filterTag === tag ? 'bg-accent/15 border-accent/40 text-accent' : 'bg-elevated border-default text-secondary hover:border-strong hover:text-primary'}`}>
                {tag}
              </button>
            ))}
          </div>
        )}
        {m.tag_values?.length > 0 && (
          <div className="flex gap-1.5 flex-wrap pt-0.5">
            {m.tag_values.map((tv, i) => (
              <span key={i} title={`${tv.dimension_name}: ${tv.value ?? tv.value_date ?? ''}`}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-accent/25 bg-accent/5 text-[11px] text-secondary">
                <span className="text-muted">{tv.dimension_name}</span>
                <span className="text-primary">{tv.value ?? tv.value_date}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    )
    return (
      <div className="group relative flex flex-col rounded-xl border border-default bg-surface/80 overflow-hidden shadow-card transition-all duration-200 hover:border-strong hover:-translate-y-0.5">
        {media}
        <div className="flex-1 flex flex-col justify-between">
          {meta}
        </div>
        <button onClick={() => deleteMut.mutate(m.id)} aria-label="delete"
          className="absolute bottom-3 right-3 p-1.5 rounded text-muted hover:text-red-400 hover:bg-base/60 opacity-0 group-hover:opacity-100 transition-all">
          <Trash2 size={14} />
        </button>
      </div>
    )
  }

  const isUpload = mode === 'upload'
  const submitting = createMut.isPending || updateMut.isPending || busy

  return (
    <div className="min-h-full px-4 sm:px-7 py-5 sm:py-7 max-w-[1500px] mx-auto">
      <PageHeader
        eyebrow="Creative Intel"
        title={t.materials.title}
        subtitle={t.materials.subtitle}
        stats={[
          { label: 'ASSETS', value: <span className="text-primary font-bold">{total}</span> },
          { label: 'FILTER', value: (filterPlatform ? platLabel(filterPlatform) : 'ALL').toUpperCase() },
          { label: 'TAG', value: filterTag ? <span className="text-accent">{filterTag}</span> : '—' },
          { label: 'PAGE', value: `${page} / ${pages}` },
        ]}
      >
        <button onClick={() => navigate('/materials/analysis')}
          className="flex items-center gap-2 px-3.5 py-2.5 rounded-lg font-data text-xs text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors">
          <Sparkles size={14} />
          <span className="hidden sm:inline">{t.materials.viewAnalysis}</span>
        </button>
        <button onClick={() => setAgentOpen(true)}
          className="flex items-center gap-2 px-3.5 py-2.5 rounded-lg font-data text-xs text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors">
          <MessagesSquare size={14} />
          <span className="hidden sm:inline">{t.materials.agent.open}</span>
        </button>
        <button onClick={exportCsv}
          className="flex items-center gap-2 px-3.5 py-2.5 rounded-lg font-data text-xs text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors">
          <DownloadIcon size={14} />
          <span className="hidden sm:inline">{t.common.export}</span>
        </button>
        <button onClick={() => { editing ? closeForm() : setShowForm(!showForm) }}
          className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold text-white bg-accent hover:brightness-110 glow-accent transition-all">
          <Plus size={15} />
          {t.materials.addMaterial}
        </button>
      </PageHeader>

      {showForm && (
        <form onSubmit={handleSubmit}
          className="reveal mt-6 rounded-2xl border border-strong bg-surface shadow-pop p-5 sm:p-6 space-y-4">
          <div className="flex items-center justify-between">
            <div className="eyebrow text-muted">{editing ? t.materials.editFormTitle : t.materials.addMaterialFormTitle}</div>
            <button type="button" onClick={closeForm} className="text-muted hover:text-primary transition-colors">
              <X size={16} />
            </button>
          </div>

          {!editing && (
            <div className="inline-flex gap-1 bg-elevated rounded-lg p-1 border border-default">
              {(['upload', 'link'] as const).map(md => (
                <button type="button" key={md} onClick={() => setMode(md)}
                  className={`px-3.5 py-1.5 rounded-md font-data text-xs transition-colors ${mode === md ? 'bg-accent text-white' : 'text-secondary hover:text-primary'}`}>
                  {md === 'link' ? t.materials.sourceLink : t.materials.sourceUpload}
                </button>
              ))}
            </div>
          )}

          {/* 标题：批量上传时按各自文件名生成，不显示标题输入。
              「自动命名」按当前已选结构化标签拼标题（P5，模板化、零 LLM）。 */}
          {!(isUpload && !editing && files.length > 1) && (
            <div className="flex items-stretch gap-2">
              <div className="flex-1 min-w-0">
                <input required={!isUpload || !!editing} placeholder={t.materials.titlePlaceholder} value={form.title}
                  onChange={e => setForm(f => ({ ...f, title: e.target.value }))} className={inputClass} />
              </div>
              <button type="button" disabled={!autoNameSuggestion}
                onClick={() => setForm(f => ({ ...f, title: autoNameSuggestion }))}
                title={autoNameSuggestion || t.materials.autoNameEmpty}
                className="shrink-0 inline-flex items-center gap-1.5 px-3 rounded-lg text-xs border border-default text-secondary hover:text-accent hover:border-accent/40 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                <Wand2 size={13} /> {t.materials.autoNameBtn}
              </button>
            </div>
          )}

          {isUpload && !editing ? (
            <div className="space-y-2">
              <div
                onDragOver={e => { e.preventDefault(); if (!dragActive) setDragActive(true) }}
                onDragEnter={e => { e.preventDefault(); setDragActive(true) }}
                onDragLeave={e => { if (e.currentTarget === e.target) setDragActive(false) }}
                onDrop={onDrop}
                className={`flex flex-col items-center justify-center gap-2 px-3 py-7 bg-elevated/40 border border-dashed rounded-xl text-sm transition-colors ${dragActive ? 'border-accent bg-elevated text-primary' : 'border-strong text-secondary'}`}
              >
                <Upload size={20} className="shrink-0 text-accent" />
                <div className="text-center px-2 max-w-full truncate">
                  {files.length === 1
                    ? `${files[0].name} (${(files[0].size / 1048576).toFixed(1)}MB)`
                    : files.length > 1 ? t.materials.filesSelected(files.length)
                    : t.materials.dropHint}
                </div>
                <div className="flex flex-wrap items-center justify-center gap-2">
                  <button type="button" onClick={() => fileInputRef.current?.click()}
                    className="px-3 py-1.5 rounded-lg bg-surface border border-default hover:border-strong text-xs text-secondary hover:text-primary transition-colors">
                    {t.materials.chooseFiles}
                  </button>
                  <span className="text-[11px] text-muted">{t.materials.or}</span>
                  <button type="button" onClick={() => folderInputRef.current?.click()}
                    className="px-3 py-1.5 rounded-lg bg-surface border border-default hover:border-strong text-xs text-secondary hover:text-primary transition-colors">
                    {t.materials.chooseFolder}
                  </button>
                  {files.length > 0 && (
                    <button type="button" onClick={() => {
                      setFiles([])
                      if (fileInputRef.current) fileInputRef.current.value = ''
                      if (folderInputRef.current) folderInputRef.current.value = ''
                    }}
                      className="px-3 py-1.5 rounded-lg text-xs text-muted hover:text-red-400 transition-colors">
                      {t.materials.clearFiles}
                    </button>
                  )}
                </div>
                <input ref={fileInputRef} type="file" accept={ACCEPT} multiple className="hidden"
                  onChange={e => acceptItems(
                    Array.from(e.target.files ?? []).map(f => ({ file: f, path: f.webkitRelativePath || '' }))
                  )} />
                {/* webkitdirectory: React 类型未声明，用 ref 回调挂 DOM 属性 */}
                <input ref={el => {
                  if (el) { el.setAttribute('webkitdirectory', ''); el.setAttribute('directory', '') }
                  folderInputRef.current = el
                }} type="file" multiple className="hidden"
                  onChange={e => acceptItems(
                    Array.from(e.target.files ?? []).map(f => ({ file: f, path: f.webkitRelativePath || '' }))
                  )} />
              </div>
              <div className="font-data text-[11px] text-muted">{t.materials.maxHint}</div>
              {files.length > 1 && <div className="text-[11px] text-muted">{t.materials.batchTitleNote}</div>}

              {/* 文件名反解打标（P0）：预填核对表。解析是建议、提交前人眼过目；
                  未命中 token 红色高亮，可上传后单条修正或先改文件名重选。 */}
              {files.length > 0 && editorDims.length > 0 && (
                <div className="rounded-xl border border-default bg-elevated/30 p-3 space-y-2">
                  <label className="flex items-center gap-2 text-xs text-secondary cursor-pointer select-none">
                    <input type="checkbox" checked={parseOn} onChange={e => setParseOn(e.target.checked)}
                      className="accent-brand-500" />
                    <span className="font-medium text-primary">{t.materials.parseToggle}</span>
                    <span className="text-[10px] text-muted">{t.materials.parseHint}</span>
                  </label>
                  {parseOn && parsedByFile.length > 0 && (
                    <>
                      <div className="text-[11px] font-data text-muted">
                        {parseUnmatchedFiles === 0
                          ? t.materials.parseAllMatched(parsedByFile.length)
                          : t.materials.parseSomeUnmatched(parseUnmatchedFiles)}
                      </div>
                      <ul className="max-h-56 overflow-y-auto space-y-1.5 pr-1">
                        {parsedByFile.map((p, i) => (
                          <li key={i} className="text-[11px] leading-relaxed">
                            <span className="text-secondary break-all">{stem(files[i].name)}</span>
                            <span className="ml-1.5 inline-flex flex-wrap gap-1 align-middle">
                              {Object.entries(p.state).map(([dimId, v]) => (
                                <span key={dimId} className="px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/25">
                                  {dimNameById.get(Number(dimId))}:{v.valueDate ?? v.optionIds.map(id => optValueById.get(id)).join('+')}
                                </span>
                              ))}
                              {p.unmatched.map((tk, k) => (
                                <span key={`u${k}`} className="px-1.5 py-0.5 rounded bg-red-500/10 text-red-400 border border-red-500/30">
                                  {t.materials.parseUnmatchedLabel} {tk}
                                </span>
                              ))}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              )}
              {queue.length > 0 && (
                <ul className="space-y-1.5 pt-1">
                  {queue.map((it, i) => (
                    <li key={i} className="flex items-center gap-2.5 text-xs">
                      <span className="shrink-0">
                        {it.status === 'done' ? <Check size={14} className="text-emerald-400" />
                          : it.status === 'error' ? <AlertCircle size={14} className="text-red-400" />
                          : it.status === 'uploading' ? <Loader2 size={14} className="text-accent animate-spin" />
                          : <span className="block w-3.5 h-3.5 rounded-full border border-default" />}
                      </span>
                      <span className="truncate flex-1 text-secondary">{it.name}</span>
                      {it.status === 'uploading' && (
                        <span className="w-24 h-1.5 bg-elevated rounded-full overflow-hidden shrink-0">
                          <span className="block h-full bg-accent transition-all duration-300" style={{ width: `${it.pct}%` }} />
                        </span>
                      )}
                      <span className={`font-data shrink-0 ${it.status === 'error' ? 'text-red-400' : it.status === 'done' ? 'text-emerald-400' : 'text-muted'}`}>
                        {it.status === 'done' ? t.materials.queueDone
                          : it.status === 'error' ? t.materials.queueFailed
                          : it.status === 'uploading' ? `${it.pct}%`
                          : t.materials.queuePending}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : mode === 'link' ? (
            <input required placeholder={t.materials.urlPlaceholder} value={form.url}
              onChange={e => setForm(f => ({ ...f, url: e.target.value }))} className={inputClass} />
          ) : (
            // 编辑已上传素材：文件本身不可换
            <div className="rounded-lg border border-dashed border-default bg-elevated/30 px-3 py-2.5 text-xs text-muted">
              {t.materials.fileNotEditable}
              {editing?.file_name && <span className="block mt-1 text-secondary truncate">{editing.file_name}</span>}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Select aria-label={t.materials.selectGame} value={form.app_id}
              onChange={v => setForm(f => ({ ...f, app_id: v }))}
              options={[{ value: '', label: t.materials.selectGame },
                ...allGames.map(g => ({ value: g.app_id, label: g.name }))]} />
            {/* 上传走文件扩展名自动判类型；link/编辑才需手选 */}
            {(!isUpload || !!editing) && (
              <Select value={form.material_type} onChange={v => setForm(f => ({ ...f, material_type: v }))}
                options={[{ value: 'video', label: t.materials.types.video },
                  { value: 'image', label: t.materials.types.image },
                  { value: 'playable', label: t.materials.types.playable }]} />
            )}
          </div>
          <input placeholder={t.materials.notesPlaceholder} value={form.notes}
            onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} className={inputClass} />

          <StructuredTagEditor materialType={editorMaterialType} appId={form.app_id || undefined}
            value={tagValues} onChange={setTagValues} />

          <div className="flex justify-end gap-2 border-t border-default pt-4">
            <button type="button" onClick={closeForm}
              className="px-4 py-2 text-sm text-secondary hover:text-primary transition-colors">{t.common.cancel}</button>
            <button type="submit" disabled={submitting}
              className="px-5 py-2 bg-accent hover:brightness-110 disabled:opacity-50 rounded-lg text-sm font-semibold text-white transition-all">
              {editing ? (updateMut.isPending ? t.common.saving : t.common.save)
                : busy ? t.materials.uploading
                : isUpload && files.length > 1 ? t.materials.addBatch(files.length)
                : createMut.isPending ? t.common.saving : t.common.save}
            </button>
          </div>
        </form>
      )}

      {/* ══ TOOLBAR ══════════════════════════════════════════ */}
      <div className="reveal reveal-2 mt-6 space-y-2.5">
        {/* 搜索 + 平台 + 游戏/类型/排序合并到一行（宽屏一行排开，窄屏自动换行） */}
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex items-center flex-1 min-w-[200px] max-w-sm rounded-lg border border-default bg-surface/60 focus-within:border-accent transition-colors">
            <span className="pl-3 pr-1 text-muted"><Search size={15} /></span>
            <input type="text" placeholder={t.materials.searchPlaceholder} value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full bg-transparent py-2.5 pr-3 text-sm text-primary placeholder:text-muted focus:outline-none" />
          </div>
          <div className="flex gap-1 p-1 rounded-lg border border-default bg-surface/60">
            {PLATFORM_TABS.map(p => {
              const label = p === '' ? t.materials.platforms.all : platLabel(p)
              const active = filterPlatform === p
              return (
                <button key={p} onClick={() => setFilterPlatform(p)}
                  className={`px-3 py-1.5 rounded-md font-data text-[11px] tracking-wide transition-colors ${active ? 'bg-accent/15 text-accent' : 'text-secondary hover:text-primary hover:bg-elevated'}`}>
                  {label}
                </button>
              )
            })}
          </div>
          <div className="w-40">
            <Select aria-label={t.materials.gameFilterAll} value={filterGame} onChange={setFilterGame}
              options={[{ value: '', label: t.materials.gameFilterAll },
                ...allGames.map(g => ({ value: g.app_id, label: g.name }))]} />
          </div>
          <div className="w-32">
            <Select aria-label={t.materials.typeFilterAll} value={filterType} onChange={setFilterType}
              options={[{ value: '', label: t.materials.typeFilterAll },
                { value: 'video', label: t.materials.types.video },
                { value: 'image', label: t.materials.types.image },
                { value: 'playable', label: t.materials.types.playable }]} />
          </div>
          <div className="w-36">
            <Select aria-label={t.materials.sortLabel} value={sort} onChange={setSort} options={sortOptions} />
          </div>
        </div>
        {/* 标签筛选栏：本地聚合，零 ST 配额；点卡片标签也会落到这里 */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-1.5 text-xs text-muted pr-1">
            <TagIcon size={13} /> {t.materials.tagFilterLabel}
          </span>
          <button onClick={() => setFilterTag('')}
            className={`px-2.5 py-1 rounded-md text-xs border transition-colors ${filterTag === '' ? 'bg-accent/15 border-accent/40 text-accent' : 'border-default text-secondary hover:border-strong hover:text-primary'}`}>
            {t.materials.tagFilterAll}
          </button>
          {tagCounts.length === 0 ? (
            <span className="text-xs text-muted/60">{t.materials.noTags}</span>
          ) : tagCounts.map(({ tag, count }) => {
            const active = filterTag === tag
            return (
              <button key={tag} onClick={() => setFilterTag(active ? '' : tag)} title={tag}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs border transition-colors ${active ? 'bg-accent/15 border-accent/40 text-accent' : 'border-default text-secondary hover:border-strong hover:text-primary'}`}>
                <span className="max-w-[160px] truncate">{tag}</span>
                <span className="font-data text-[10px] text-muted">{count}</span>
              </button>
            )
          })}
        </div>
        {/* 结构化分面筛选栏（P3）：按一级标签分组的二级标签 chip；同维度内 OR、跨维度 AND。
            本地 SQLite 过滤，零 ST 配额。仅在库里存在文字型一级标签时出现。 */}
        {facetable.length > 0 && (
          <div className="space-y-1.5 rounded-lg border border-default/60 bg-surface/40 px-3 py-2.5">
            {/* 标签包切换（切片 2）：选了具体游戏且该产品有可见包时出现。
                开关关闭 → 只显示一个低调的「启用」入口，界面其余与现状一致。 */}
            {filterGame && packs.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 border-b border-default/50 pb-2">
                <span className="flex items-center gap-1.5 text-xs text-muted">
                  <Layers size={13} /> {t.materials.packSwitchLabel}
                </span>
                {packsEnabled ? (<>
                  <button onClick={() => setActivePackId(null)}
                    className={`px-2.5 py-0.5 rounded-md text-xs border transition-colors ${!currentPack ? 'bg-accent/15 border-accent/40 text-accent' : 'border-default text-secondary hover:border-strong hover:text-primary'}`}>
                    {t.materials.packAll}
                  </button>
                  {packs.map(p => (
                    <button key={p.id} onClick={() => setActivePackId(p.id)} title={p.name}
                      className={`px-2.5 py-0.5 rounded-md text-xs border transition-colors ${currentPack?.id === p.id ? 'bg-accent/15 border-accent/40 text-accent' : 'border-default text-secondary hover:border-strong hover:text-primary'}`}>
                      {p.name}
                    </button>
                  ))}
                  {currentPack && (
                    <label className="flex items-center gap-1.5 text-[11px] text-secondary cursor-pointer select-none ml-2">
                      <input type="checkbox" checked={packTaggedOnly}
                        onChange={e => setPackTaggedOnly(e.target.checked)}
                        className="accent-brand-500" />
                      {t.materials.packTaggedOnly}
                    </label>
                  )}
                  <button onClick={() => packSettingMut.mutate(false)} disabled={packSettingMut.isPending}
                    className="text-[11px] text-muted hover:text-red-400 transition-colors ml-auto">
                    {t.materials.packDisableBtn}
                  </button>
                </>) : (
                  <button onClick={() => packSettingMut.mutate(true)} disabled={packSettingMut.isPending}
                    className="text-[11px] text-muted hover:text-accent transition-colors">
                    {t.materials.packEnableBtn}
                  </button>
                )}
              </div>
            )}
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1.5 text-xs text-muted">
                <SlidersHorizontal size={13} /> {t.materials.facetFilterLabel}
              </span>
              {filterOptions.size > 0 && (
                <button onClick={() => setFilterOptions(new Set())}
                  className="text-[11px] text-muted hover:text-red-400 transition-colors">
                  {t.materials.facetClear(filterOptions.size)}
                </button>
              )}
            </div>
            {/* 通用维度：常驻显示 */}
            {facetGroups.universal.map(d => (
              <FacetRow key={d.id} d={d} filterOptions={filterOptions} onToggle={toggleFacet} />
            ))}
            {/* 各产品专属维度：分组折叠（默认收起，含已选项或手动展开则显示） */}
            {facetGroups.groups.map(g => {
              const open = expandedFacetGroups.includes(g.key) || g.activeCount > 0
              return (
                <div key={g.key} className="border-t border-default/50 pt-1.5 mt-0.5">
                  <button onClick={() => toggleFacetGroup(g.key)}
                    className="flex items-center gap-1 text-[11px] text-muted hover:text-primary transition-colors">
                    {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    {t.materials.facetGroupScoped(g.label, g.dims.length)}
                    {g.activeCount > 0 && (
                      <span className="text-accent">· {t.materials.facetGroupActive(g.activeCount)}</span>
                    )}
                  </button>
                  {open && (
                    <div className="space-y-1.5 mt-1.5">
                      {g.dims.map(d => (
                        <FacetRow key={d.id} d={d} filterOptions={filterOptions} onToggle={toggleFacet} />
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
            {/* 选包视图下的「未分组」段：不属于当前包的维度收在这（默认折叠），
                防止"进了包视图就找不到某个维度"；有选中项时自动展开。 */}
            {ungroupedFacetable.length > 0 && (() => {
              const open = expandedFacetGroups.includes('__unpacked__') || ungroupedActiveCount > 0
              return (
                <div className="border-t border-default/50 pt-1.5 mt-0.5">
                  <button onClick={() => toggleFacetGroup('__unpacked__')}
                    className="flex items-center gap-1 text-[11px] text-muted hover:text-primary transition-colors">
                    {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    {t.materials.packUngrouped(ungroupedFacetable.length)}
                    {ungroupedActiveCount > 0 && (
                      <span className="text-accent">· {t.materials.facetGroupActive(ungroupedActiveCount)}</span>
                    )}
                  </button>
                  {open && (
                    <div className="space-y-1.5 mt-1.5">
                      {ungroupedFacetable.map(d => (
                        <FacetRow key={d.id} d={d} filterOptions={filterOptions} onToggle={toggleFacet} />
                      ))}
                    </div>
                  )}
                </div>
              )
            })()}
          </div>
        )}
        {/* 聚合分析（P4）：scope 跟随上方 material_type + 分面筛选；零 ST 配额。
            AI 标签分析入口已上移到页头（与「AI 解析报告」并排），抽屉在下方挂载。 */}
        <TagAggregatePanel dims={facetable} materialType={filterType || undefined} tagOptions={facetKey || undefined} />
      </div>

      <TagAnalysisAgent
        open={agentOpen}
        onClose={() => setAgentOpen(false)}
        materialType={filterType || undefined}
        appId={filterGame || undefined}
        tagOptions={facetKey || undefined}
      />

      {/* 批量「按标签命名」/「按标题解析补标」：互为镜像的两个方向，都只对本页生效 */}
      {(materials.some(m => m.tag_values?.length) || materials.some(m => m.app_id && !m.tag_values?.length)) && (
        <div className="reveal reveal-3 mt-6 flex justify-end gap-2">
          {materials.some(m => m.app_id && !m.tag_values?.length) && (
            <button type="button" onClick={fillTagsByTitleBatch}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border border-default text-secondary hover:text-accent hover:border-accent/40 transition-colors">
              <Wand2 size={13} /> {t.materials.fillByTitleBatch}
            </button>
          )}
          {materials.some(m => m.tag_values?.length) && (
            <button type="button" onClick={openRenameBatch}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border border-default text-secondary hover:text-accent hover:border-accent/40 transition-colors">
              <Wand2 size={13} /> {t.materials.nameByTagsBatch}
            </button>
          )}
        </div>
      )}

      {/* ══ GRID ══════════════════════════════════════════════ */}
      <div className="reveal reveal-3 mt-6">
        {isError ? (
          <QueryError onRetry={() => refetch()} />
        ) : isLoading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="rounded-xl border border-default bg-surface/60 overflow-hidden">
                <div className="aspect-video bg-elevated animate-pulse" />
                <div className="p-4 space-y-2">
                  <div className="h-2.5 w-1/3 bg-elevated rounded animate-pulse" />
                  <div className="h-4 w-3/4 bg-elevated rounded animate-pulse" />
                </div>
              </div>
            ))}
          </div>
        ) : materials.length === 0 ? (
          <div className="hud relative flex flex-col items-center justify-center py-24 rounded-2xl border border-default bg-surface/40 text-center">
            <Radio size={26} className="text-muted/50 mb-3" />
            <div className="eyebrow text-muted">No Signal</div>
            <p className="text-secondary text-sm mt-2">
              {debouncedSearch || filterPlatform || filterType || filterGame || filterTag || filterOptions.size ? t.common.noResult : t.materials.empty}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {materials.map((m, i) => (
              <AssetCard key={m.id} m={m} n={offset + i + 1} />
            ))}
          </div>
        )}
      </div>

      <div className="reveal reveal-4 mt-7">
        <Pagination total={total} offset={offset} pageSize={PAGE_SIZE} onOffsetChange={setOffset} />
      </div>

      <MaterialAnalysisDrawer material={analyzing} onClose={() => setAnalyzing(null)} />

      {/* 批量重命名预览 modal：列旧名→新名，看清后果再确认（治误触 + 「确认了不知道改成啥」）。 */}
      {renamePlan && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => { if (!renaming) setRenamePlan(null) }}>
          <div className="bg-surface border border-strong rounded-2xl shadow-pop w-full max-w-lg p-5 space-y-4"
            onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <div className="eyebrow text-muted">{t.materials.renamePreviewTitle}</div>
              <button onClick={() => { if (!renaming) setRenamePlan(null) }} className="text-muted hover:text-primary transition-colors">
                <X size={16} />
              </button>
            </div>
            <p className="text-xs text-secondary">{t.materials.renamePreviewHint(renamePlan.length)}</p>
            <div className="max-h-72 overflow-y-auto space-y-1.5 rounded-lg border border-default bg-elevated/30 p-2.5">
              {renamePlan.slice(0, 30).map(it => (
                <div key={it.id} className="text-[11px] leading-snug">
                  <span className="text-muted line-through break-all">{it.oldTitle}</span>
                  <span className="text-accent mx-1.5">→</span>
                  <span className="text-primary break-all">{it.newTitle}</span>
                </div>
              ))}
              {renamePlan.length > 30 && (
                <div className="text-[11px] text-muted pt-1">… {t.materials.renamePreviewMore(renamePlan.length - 30)}</div>
              )}
            </div>
            <div className="flex justify-end gap-2 border-t border-default pt-4">
              <button onClick={() => setRenamePlan(null)} disabled={renaming}
                className="px-4 py-2 text-sm text-secondary hover:text-primary disabled:opacity-50 transition-colors">{t.common.cancel}</button>
              <button onClick={confirmRenameBatch} disabled={renaming}
                className="px-5 py-2 bg-accent hover:brightness-110 disabled:opacity-50 rounded-lg text-sm font-semibold text-white transition-all">
                {renaming ? t.common.saving : t.materials.renamePreviewConfirm(renamePlan.length)}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
