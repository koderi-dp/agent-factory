const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const source = fs.readFileSync(path.join(__dirname, '../factory/dashboard/dashboard.js'), 'utf8')
function context() {
  const ctx = vm.createContext({})
  // Load the actual board helpers without starting the page's polling/event handlers.
  vm.runInContext(source.slice(0, source.indexOf('// ---- Time')), ctx)
  vm.runInContext(source.slice(source.indexOf('function chips('), source.indexOf('function brokenCard(')), ctx)
  return ctx
}
const plain = (value) => JSON.parse(JSON.stringify(value))

test('columns include current config and frozen pipelines from older jobs', () => {
  const ctx = context()
  vm.runInContext(`backend = { pipeline: [{id:'draft', label:'Draft', agents:['writer']}, {id:'gate', label:'Gate', agents:['approver']}] };
    jobs = [{pipeline:[{id:'old-check',label:'Old check',agents:['old-reviewer']}],stage:'old-check'}]`, ctx)
  assert.deepEqual(plain(vm.runInContext('boardStages().map(s => s.key)', ctx)), ['draft', 'gate', 'old-check', 'merged', 'needs-human'])
  assert.equal(vm.runInContext('isLive(jobs[0])', ctx), true)
  assert.equal(vm.runInContext("isLive({stage:'merged',pipeline:jobs[0].pipeline})", ctx), false)
})

test('custom reviewer chips have their own names and verdicts', () => {
  const ctx = context()
  const html = vm.runInContext("chips(2, {'quality':'PASS', 'risk':'CHANGES'}, false, [['quality','quality'], ['risk','risk']])", ctx)
  assert.match(html, /quality: PASS/)
  assert.match(html, /risk: CHANGES/)
  assert.doesNotMatch(html, /security|ux|ui|code/)
})

test('legacy jobs retain default columns', () => {
  const ctx = context()
  assert.deepEqual(plain(vm.runInContext('boardStages().map(s => s.key)', ctx)), ['spec', 'build', 'review', 'approve', 'merged', 'needs-human'])
})

function settingsContext() {
  const ctx = context()
  const controls = new Map()
  const elements = {
    's-pipeline': { value: JSON.stringify([
      { id: 'spec', uses: 'spec-writer', providers: {
        claude: { model: 'opus', effort: 'medium' }, codex: { model: 'gpt-6.1-sol', effort: 'medium' },
      } },
      { id: 'review', parallel: [{ id: 'security', uses: 'reviewer-security', providers: {
        claude: { model: 'sonnet', effort: 'high' }, codex: { model: 'gpt-6-luna', effort: 'low' },
      } }] },
    ]) },
    's-models': { innerHTML: '', querySelector: (selector) => controls.get(selector) },
  }
  ctx.document = { getElementById: (id) => elements[id] }
  vm.runInContext(source.slice(source.indexOf('function options('), source.indexOf('function openSettings(')), ctx)
  vm.runInContext(`backend = { config: {provider: 'claude'}, choices: {
    claude: {models: ['opus','sonnet','haiku'], efforts: ['low','medium','high']},
    codex: {models: ['gpt-6.1-sol','gpt-6-luna'], efforts: ['low','medium','high']}
  } }; renderAgentSettings('claude')`, ctx)
  for (const [key, model, effort] of [['0', 'haiku', 'low'], ['1/0', 'sonnet', 'high']]) {
    controls.set(`[data-model="${key}"]`, { value: model })
    controls.set(`[data-effort="${key}"]`, { value: effort })
  }
  return { ctx, elements }
}

test('settings write per-execution provider values and preserve the other client', () => {
  const { ctx, elements } = settingsContext()
  assert.match(elements['s-models'].innerHTML, /review \/ security/)
  assert.doesNotMatch(elements['s-models'].innerHTML, /value="default"/)
  vm.runInContext('storeAgentSettings()', ctx)
  const steps = JSON.parse(elements['s-pipeline'].value)
  assert.deepEqual(steps[0].providers.claude, { model: 'haiku', effort: 'low' })
  assert.deepEqual(steps[0].providers.codex, { model: 'gpt-6.1-sol', effort: 'medium' })
  assert.deepEqual(steps[1].parallel[0].providers.claude, { model: 'sonnet', effort: 'high' })
})

test('manual JSON edits take priority over previously rendered model controls', () => {
  const { ctx, elements } = settingsContext()
  const steps = JSON.parse(elements['s-pipeline'].value)
  steps[0].providers.claude = { model: 'sonnet', effort: 'medium' }
  elements['s-pipeline'].value = JSON.stringify(steps)
  vm.runInContext('storeAgentSettings()', ctx)
  assert.deepEqual(JSON.parse(elements['s-pipeline'].value), steps)
  vm.runInContext("renderAgentSettings('codex')", ctx)
  assert.match(elements['s-models'].innerHTML, /gpt-6-luna/)
})

test('column labels use the configured execution provider settings', () => {
  const { ctx } = settingsContext()
  assert.equal(vm.runInContext(`whoOf({executions: [{id:'spec',providers:{claude:{model:'opus',effort:'medium'}}}]})`, ctx), 'spec · opus · medium')
})
