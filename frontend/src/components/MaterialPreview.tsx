import type { MaterialOut } from '../lib/types'

/**
 * 上传类素材的站内预览：视频用原生 <video controls>（preload=metadata，点开
 * 才取流，后端支持 Range 可拖拽进度条）；图片直接 <img>。外链素材返回 null，
 * 由调用方保留原有「外链」展示。
 */
export function MaterialPreview({ m }: { m: MaterialOut }) {
  if (m.source !== 'upload' || !m.stream_url) return null
  const isVideo = m.material_type === 'video' || (m.mime_type ?? '').startsWith('video/')
  return isVideo ? (
    <video
      src={m.stream_url}
      controls
      preload="metadata"
      className="mt-2 w-full max-h-64 rounded-lg bg-black"
    />
  ) : (
    <img
      src={m.stream_url}
      alt={m.title}
      loading="lazy"
      className="mt-2 w-full max-h-64 object-contain rounded-lg bg-black"
    />
  )
}
