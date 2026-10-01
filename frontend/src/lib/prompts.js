// Builds the prompts sent to arena2api for each roadmap step.

const FORMAT_INSTRUCTIONS = [
  'You will complete ONE step at a time. For every step, output each file you create or',
  'modify in full - never partial snippets or diffs. Keep explanations short and put all',
  'code inside fenced blocks. Use fenced code blocks with the file path as the language',
  'info string, like:',
].join('\n')

export function buildPrompt({ projectContext, steps, stepIndex, previousFiles }) {
  const step = steps[stepIndex - 1]
  const total = steps.length
  const current = [
    `CURRENT STEP ${stepIndex} of ${total}: ${step.title}`,
    'DETAILS:',
    step.description || '(no additional details)',
    '',
    'Produce the files for this step now.',
  ].join('\n')

  if (stepIndex === 1) {
    return [
      'You are building a project by following a ROADMAP.md step by step.',
      '',
      'PROJECT CONTEXT:',
      (projectContext || '').trim() || '(none provided)',
      '',
      FORMAT_INSTRUCTIONS,
      '```python:src/main.py',
      '<file content>',
      '```',
      '',
      current,
    ].join('\n')
  }

  const files = previousFiles && previousFiles.length
    ? previousFiles.map((p) => `- ${p}`).join('\n')
    : '- (none yet)'
  return [
    'PREVIOUSLY CREATED FILES (do not recreate unless modifying):',
    files,
    '',
    current,
  ].join('\n')
}
