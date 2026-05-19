import type { MaterialOut } from '../lib/types'

/**
 * 上传类素材的站内预览：视频用原生 <video controls>（preload=metadata，点开
 * 才取流，后端支持 Range 可拖拽进度条）；图片直接 <img>。外链素材返回 null，
 * 由调用方保留原有「外链」展示。
 *
 * fill=true：绝对填充父容器（父需 relative + 定高，如素材库卡片的 aspect-video
 * 媒体区）。默认 false：原块状（mt-2 限高），供游戏详情页等普通流式布局使用。
 */
export function MaterialPreview({ m, fill = false }: { m: MaterialOut; fill?: boolean }) {
  if (m.source !== 'upload' || !m.stream_url) return null
  const isVideo = m.material_type === 'video' || (m.mime_type ?? '').startsWith('video/')
  const cls = fill
    ? 'absolute inset-0 w-full h-full bg-black'
    : 'mt-2 w-full max-h-64 rounded-lg bg-black'
  return isVideo ? (
    <video
      src={m.stream_url}
      controls
      preload="metadata"
      className={`${cls} ${fill ? 'object-contain' : ''}`}
    />
  ) : (
    <img
      src={m.stream_url}
      alt={m.title}
      loading="lazy"
      className={`${cls} ${fill ? 'object-cover' : 'object-contain'}`}
    />
  )
}
