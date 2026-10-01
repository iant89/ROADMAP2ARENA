const EXT_LANG = {
  py: 'python', js: 'javascript', jsx: 'jsx', ts: 'typescript', tsx: 'tsx', md: 'markdown',
  toml: 'toml', yml: 'yaml', yaml: 'yaml', json: 'json', sh: 'bash', txt: 'text', html: 'html',
  css: 'css', sql: 'sql', go: 'go', rs: 'rust', java: 'java', env: 'dotenv',
}

export function guessLang(path) {
  const base = (path || '').split(/[\\/]/).pop() || ''
  if (base === 'Dockerfile') return 'dockerfile'
  if (base.startsWith('.env')) return 'dotenv'
  const ext = base.includes('.') ? base.split('.').pop().toLowerCase() : ''
  return EXT_LANG[ext] || 'text'
}
