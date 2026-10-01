// Extracts file artifacts from a model response.
//
// A fenced block becomes a named file when:
//   - its info string is "lang:path" (e.g. ```python:src/main.py), or
//   - its first line is a filename comment:
//       "# filename: x", "// filename: x" or "<!-- path: x -->"
//     (the marker line is removed from the file content).
// Otherwise the block is unnamed: it stays in the transcript only and is
// never placed in the file tree or the ZIP.

const OPEN_FENCE = /^(\s*)(`{3,}|~{3,})\s*([^\s`]*)\s*$/
const COMMENT_PATTERNS = [
  /^\s*#\s*(?:filename|file|path)\s*:\s*(\S.*?)\s*$/i,
  /^\s*\/\/\s*(?:filename|file|path)\s*:\s*(\S.*?)\s*$/i,
  /^\s*<!--\s*(?:filename|file|path)\s*:\s*(\S.*?)\s*(?:-->)?\s*$/i,
]

export function extractBlocks(response) {
  const lines = (response || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks = []
  let open = null

  for (const line of lines) {
    if (!open) {
      const m = line.match(OPEN_FENCE)
      if (m) open = { fence: m[2], info: m[3] || '', body: [] }
      continue
    }
    const trimmed = line.trim()
    if (trimmed.startsWith(open.fence[0].repeat(open.fence.length)) && trimmed.replace(/[`~]/g, '') === '') {
      blocks.push(toBlock(open))
      open = null
      continue
    }
    open.body.push(line)
  }
  if (open) blocks.push(toBlock(open))
  return blocks
}

function toBlock({ info, body }) {
  const colon = info.indexOf(':')
  if (colon > -1 && colon < info.length - 1) {
    return { lang: info.slice(0, colon) || 'text', path: info.slice(colon + 1), content: body.join('\n') }
  }
  const first = body[0] ?? ''
  for (const pattern of COMMENT_PATTERNS) {
    const m = first.match(pattern)
    if (m) {
      return { lang: info || guessLang(m[1]), path: m[1], content: body.slice(1).join('\n') }
    }
  }
  return { lang: info || 'text', path: null, content: body.join('\n') }
}

export function extractArtifacts(response) {
  return extractBlocks(response).filter((b) => b.path)
}

// Merge a step's artifacts into the running map; later steps replace earlier
// versions of the same path. Returns a new map.
export function mergeArtifacts(map, stepIndex, response) {
  const next = { ...map }
  for (const block of extractArtifacts(response)) {
    const prev = next[block.path]
    next[block.path] = {
      path: block.path,
      lang: block.lang,
      content: block.content,
      step_index: stepIndex,
      history: prev ? [...prev.history, stepIndex] : [stepIndex],
    }
  }
  return next
}

const EXT_LANG = {
  py: 'python', js: 'javascript', jsx: 'jsx', ts: 'typescript', md: 'markdown',
  toml: 'toml', yml: 'yaml', yaml: 'yaml', json: 'json', sh: 'bash', txt: 'text',
}

export function guessLang(path) {
  const base = (path || '').split('/').pop() || ''
  if (base === 'Dockerfile') return 'dockerfile'
  const ext = base.includes('.') ? base.split('.').pop().toLowerCase() : ''
  return EXT_LANG[ext] || 'text'
}
