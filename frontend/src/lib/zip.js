import JSZip from 'jszip'

// Normalises an artifact path for the ZIP. Returns { path } or { skip: reason }.
export function cleanZipPath(raw) {
  let p = String(raw ?? '').trim().replace(/\\/g, '/')
  let prev
  do {
    prev = p
    p = p.replace(/^\/+/, '').replace(/^(\.\/)+/, '')
  } while (p !== prev)
  const segments = p.split('/').filter((s) => s !== '' && s !== '.')
  if (segments.some((s) => s === '..')) return { skip: 'contains a ".." segment' }
  if (!segments.length || p.endsWith('/')) return { skip: 'empty file name' }
  return { path: segments.join('/') }
}

// Builds a ZIP blob from an artifacts list. Unnamed blocks never reach here.
export async function buildZip(artifacts) {
  const zip = new JSZip()
  const included = []
  const skipped = []
  const seen = new Set()
  for (const a of artifacts) {
    const res = cleanZipPath(a.path)
    if (res.skip) {
      skipped.push({ path: a.path, reason: res.skip })
      continue
    }
    if (seen.has(res.path)) {
      skipped.push({ path: a.path, reason: `duplicate of ${res.path}` })
      continue
    }
    seen.add(res.path)
    zip.file(res.path, a.content.endsWith('\n') ? a.content : `${a.content}\n`)
    included.push(res.path)
  }
  const blob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' })
  return { blob, included, skipped }
}

export function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
