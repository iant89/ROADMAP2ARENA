// Parses a ROADMAP.md into an ordered list of steps.
//
// Rules:
// 1. "### <title>" starts a step; its body runs until the next "###" heading
//    (or EOF) and becomes the step description.
// 2. "- [ ] <title>" outside a ### block is a standalone step (no description).
// 3. Anything else outside a ### block is ignored (other headings, paragraphs,
//    checked boxes "- [x]").
// 4. Fenced code inside a step body is kept verbatim; a "###" inside a fence
//    does not start a new step.

const STEP_HEADING = /^###\s+(.+?)\s*#*\s*$/
const UNCHECKED_ITEM = /^\s*[-*+]\s+\[ \]\s+(.+?)\s*$/
const FENCE = /^\s*(```|~~~)/

export function parseRoadmap(markdown) {
  const text = (markdown || '').replace(/\r\n?/g, '\n')
  const lines = text.split('\n')
  const steps = []
  let current = null
  let inFence = false

  const closeCurrent = () => {
    if (!current) return
    current.description = current.body.join('\n').trim()
    delete current.body
    steps.push(current)
    current = null
  }

  for (const line of lines) {
    if (current && FENCE.test(line)) {
      inFence = !inFence
      current.body.push(line)
      continue
    }
    if (!inFence) {
      const heading = line.match(STEP_HEADING)
      if (heading) {
        closeCurrent()
        current = { title: heading[1].trim(), body: [] }
        continue
      }
    }
    if (current) {
      current.body.push(line)
      continue
    }
    const item = line.match(UNCHECKED_ITEM)
    if (item) {
      steps.push({ title: item[1].trim(), description: '' })
    }
  }
  closeCurrent()

  return steps.map((s, i) => ({ index: i + 1, title: s.title, description: s.description }))
}
