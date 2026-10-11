// Made-up turns for the design page: the conversation in each state, fed to the
// real components. Everything here is invented.

import type { ChatMessage, Citation, ToolRun } from '../chat/types'

let next = 1
function turn(role: 'user' | 'assistant', content: string, extra: Partial<ChatMessage> = {}): ChatMessage {
  return { id: next++, role, content, citations: [], tools: [], ...extra }
}

const search: ToolRun = {
  name: 'files_search',
  result: 'Found 3 files: roof-repair.md, house-to-do.md, quotes/roofers.txt',
  ok: true,
  code: '',
}

const roofSources: Citation[] = [
  { n: 1, title: 'roof-repair.md', ordinal: 0, text: "Roofer (Ash & Sons) can start on the 30th, two days' work, scaffolding included." },
  { n: 2, title: 'house-to-do.md', ordinal: 2, text: 'Roof: sort before the October rain. Back bedroom ceiling stain is getting bigger.' },
]

export const roofQuestion = turn('user', 'What did I note about the roof last month?')
export const roofAnswer = turn(
  'assistant',
  'You noted a leak above the back bedroom on 2 September and asked two roofers for a date. The first can start on the 30th [1], and you wanted it done before the October rain [2].\n\nWant me to add "Book the roofer for the 30th" to your house list?',
  { tools: [search], citations: roofSources },
)

export const weekQuestion = turn('user', 'Plan my week around the dentist on Thursday')
export const weekPartial = turn('assistant', "Here's a week that keeps Thursday light:\n\n1. **Monday**: the quarterly report outline, while your head", {
  streaming: true,
})

export const writeQuestion = turn('user', 'Add "Book the roofer for the 30th" to my house list')
export const writePending = turn('assistant', '', { tools: [], streaming: true })

export const giftQuestion = turn('user', 'Birthday gift ideas for Morgan')

export const richQuestion = turn('user', 'Compare the two roofers and tell me what to do next')
export const richAnswer = turn(
  'assistant',
  [
    '## The two quotes',
    '',
    '| Roofer | Price | Can start | Scaffolding |',
    '|:---|---:|:---:|:---|',
    '| Ash & Sons | £2,400 | 30 Sep | included |',
    '| Birch Roofing | £2,150 | 14 Oct | £180 extra |',
    '',
    'Birch is cheaper on paper, but with scaffolding it is $2{,}150 + 180 = 2{,}330$, only £70 less, and it starts two weeks later.',
    '',
    '### What I would do',
    '',
    '1. **Book Ash & Sons** for the 30th, before the rain.',
    '',
    '2. **Ask about the gutters** while the scaffolding is up:',
    '   - the back one overflows',
    '   - the front downpipe rattles',
    '',
    '3. Keep the Birch quote in `quotes/roofers.txt` in case the date slips.',
    '',
    '> Measure twice, pay once.',
    '',
    'A reply you could send:',
    '',
    '```text',
    'Hi Ash, the 30th works for us. Could you also look at the gutters?',
    '```',
  ].join('\n'),
  { tools: [search], citations: roofSources },
)
