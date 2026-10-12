const POLL_MS = 2000
const QUIET_MS = 3 * 60 * 60 * 1000 // a live job with no job.json write for this long may have lost its session
const MERGED_SHOWN = 5
const STAGES = [
  { key: 'spec', label: 'Spec', agent: 'spec-writer', who: 'spec-writer · opus' },
  { key: 'build', label: 'Build', agent: 'builder', who: 'builder · opus' },
  { key: 'review', label: 'Review', who: 'sec · ux · ui · code' },
  { key: 'approve', label: 'Approve', agent: 'approver', who: 'approver · opus' },
  { key: 'merged', label: 'Merged', who: 'main', cls: 'merged-col' },
  { key: 'needs-human', label: 'Needs human', who: 'approve · rework', cls: 'human' },
]
// Jobs approved under the "verify" merge policy wait in Needs human for the user to merge.
const COLUMN = { approved: 'needs-human' }
const LIVE = new Set(['spec', 'build', 'review', 'approve'])
const MERGEABLE = new Set(['approve', 'approved', 'needs-human'])
const REVIEWERS = [['security', 'sec'], ['ux', 'ux'], ['ui', 'ui'], ['code', 'code']]

// Set when the page is served by factory/server.py: settings, merge, delete and backlog edits.
let backend = null

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { 'X-Factory': '1', 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
  return data
}

function modelOf(agent) {
  if (!backend) return null
  const set = backend.config.models[agent]
  return set && set !== 'default' ? set : backend.agentDefaults[agent] || 'default'
}

function whoOf(stage) {
  if (!backend) return stage.who
  if (stage.agent) return `${stage.agent} · ${modelOf(stage.agent)}`
  if (stage.key === 'review') {
    const models = new Set(REVIEWERS.map(([k]) => modelOf(`reviewer-${k}`)))
    return `sec · ux · ui · code · ${[...models].join('/')}`
  }
  return stage.who
}

const $ = (id) => document.getElementById(id)
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])

let jobs = []
let backlog = []
let lastRender = ''
let showAllMerged = false
const roundCache = new Map() // "<job>/<n>" -> verdicts of a finished round
const goodJobs = new Map() // id -> last job.json that parsed, so a half-written file doesn't flicker
const decisions = new Map() // id -> { updated, verdict, followUps } from decision.md
let openJobId = null
let openPath = null
let openText = null
let opener = null

async function fetchText(url) {
  const res = await fetch(url, { cache: 'no-store' })
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`)
  return res.text()
}

async function fetchOptional(url) {
  const res = await fetch(url, { cache: 'no-store' })
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`)
  return res.text()
}

// ---- Time ----------------------------------------------------------------

function ago(ms) {
  const m = Math.max(0, Math.round(ms / 60000))
  if (m < 1) return '<1m'
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h}h ${String(m % 60).padStart(2, '0')}m`
  return `${Math.floor(h / 24)}d`
}

const at = (s) => (s ? Date.parse(s) : NaN)

function stageSince(job) {
  const last = (job.history || []).at(-1)
  return last ? at(last.at) : NaN
}

function took(job) {
  const start = at(job.created || (job.history || [])[0]?.at)
  const end = stageSince(job)
  return start && end && end > start ? end - start : NaN
}

// ---- Board ---------------------------------------------------------------

function verdictOf(text) {
  const m = text.split('\n')[0].trim().match(/^VERDICT:\s*(PASS|CHANGES)$/)
  return m ? m[1] : 'invalid'
}

// Earlier rounds aren't in job.json; their verdicts come from the review files.
async function pastRounds(job) {
  const rounds = []
  for (let n = 1; n < job.round; n++) {
    const key = `${job.id}/${n}`
    if (!roundCache.has(key)) {
      const verdicts = {}
      await Promise.all(REVIEWERS.map(async ([k]) => {
        try {
          verdicts[k] = verdictOf(await fetchText(`jobs/${job.id}/round-${n}/review-${k}.md`))
        } catch {
          verdicts[k] = 'missing'
        }
      }))
      if (Object.values(verdicts).every((v) => v === 'PASS' || v === 'CHANGES')) {
        roundCache.set(key, verdicts)
      } else {
        rounds.push(verdicts)
        continue
      }
    }
    rounds.push(roundCache.get(key))
  }
  return rounds
}

// Lines under "## Follow-ups" in decision.md, not counting "None.".
function countFollowUps(text) {
  const lines = text.replace(/\r/g, '').split('\n')
  const start = lines.findIndex((l) => /^##\s+Follow-ups\s*$/i.test(l))
  if (start < 0) return 0
  let count = 0
  for (const l of lines.slice(start + 1)) {
    if (/^#{1,2}\s/.test(l)) break
    if (/^\s{0,1}([-*+]|\d+\.)\s+/.test(l)) count++
  }
  return count
}

async function decisionOf(job) {
  const none = { verdict: null, followUps: 0 }
  if (!['merged', 'needs-human', 'approved'].includes(job.stage)) return none
  const cached = decisions.get(job.id)
  if (cached && cached.updated === job.updated) return cached
  const text = await fetchOptional(`jobs/${job.id}/decision.md`).catch(() => null)
  const d = text
    ? { updated: job.updated, verdict: text.split('\n')[0].trim(), followUps: countFollowUps(text) }
    : { updated: job.updated, ...none }
  decisions.set(job.id, d)
  return d
}

function chips(n, verdicts, waiting) {
  const cells = REVIEWERS.map(([k, short]) => {
    const v = verdicts[k]
    const cls = v === 'PASS' || v === 'CHANGES' ? v : v === 'pending' ? (waiting ? 'wait' : '') : 'bad'
    const label = v === 'missing' || v === 'invalid' ? `${short}?` : short
    return `<span class="chip ${cls}" title="${k}: ${esc(v)}">${label}</span>`
  }).join('')
  return `<div class="round"><span class="r">R${n}</span>${cells}</div>`
}

function brokenCard(job) {
  const [num, ...slug] = job.id.split('-')
  return `
    <div class="card broken">
      <div class="card-top"><span class="num">${esc(num)} · ${esc(slug.join('-'))}</span></div>
      <p class="feature note">${esc(job.problem)}</p>
    </div>`
}

function card(job, past, decision, now) {
  if (job.problem) return brokenCard(job)
  const live = LIVE.has(job.stage)
  const human = job.stage === 'needs-human' || job.stage === 'approved'
  // Approved jobs wait here under the "verify" policy, or when the main checkout was busy at merge time.
  const parked = job.stage === 'approved' || (human && decision.verdict === 'APPROVE')
  const fu = decision.followUps
  const since = stageSince(job)
  const quiet = live && now - at(job.updated) > QUIET_MS
  const rows = past.map((v, i) => chips(i + 1, v, false))
  if (job.round > 0) rows.push(chips(job.round, job.reviews || {}, job.stage === 'review'))
  const [num, ...slug] = job.id.split('-')
  const meta = []
  if (live && since) meta.push(`<span title="in ${esc(job.stage)} since ${esc(new Date(since).toLocaleString())}">in ${esc(job.stage)} ${ago(now - since)}</span>`)
  if (!live && took(job)) meta.push(`<span title="from start to ${esc(job.stage)}">took ${ago(took(job))}</span>`)
  if (!live && since) meta.push(`<span>${ago(now - since)} ago</span>`)
  if (fu) meta.push(`<span class="fu">${fu} follow-up${fu === 1 ? '' : 's'}</span>`)
  const verify = job.stage === 'approved'
  const hint = parked && backend
    ? `${verify ? 'Approved. Check it, then open the card to merge.' : 'Approved. Clean the main checkout, then open the card to merge.'}${job.note ? `<br>${esc(job.note)}` : ''}`
    : parked
    ? `Approved. ${verify ? 'Check it, then merge' : 'Merge once the main checkout is clean'}:<br>/factory approve ${esc(job.id)}`
    : human
    ? `${job.note ? `${esc(job.note)}<br>` : ''}/factory approve ${esc(job.id)}<br>/factory rework ${esc(job.id)} …`
    : quiet ? `No update for ${ago(now - at(job.updated))}. If its session ended:<br>/factory resume ${esc(job.id)}` : ''
  return `
    <button class="card ${live ? 'live' : ''} ${human ? 'human' : ''} ${job.stage === 'merged' ? 'merged' : ''}"
            data-job="${esc(job.id)}">
      <div class="card-top">
        ${live ? `<span class="pulse ${quiet ? 'still' : ''}" aria-label="${quiet ? 'quiet' : 'running'}"></span>` : ''}
        <span class="num">${esc(num)} · ${esc(slug.join('-'))}</span>
        ${parked ? '<span class="flag ok">approved</span>' : human ? '<span class="flag">your call</span>' : quiet ? '<span class="flag quiet">quiet</span>' : ''}
      </div>
      <p class="feature">${esc(job.feature)}</p>
      <div class="branch">${esc(job.branch)}</div>
      ${meta.length ? `<div class="meta">${meta.join('')}</div>` : ''}
      ${rows.length ? `<div class="rounds">${rows.join('')}</div>` : ''}
      ${hint ? `<div class="hint">${hint}</div>` : ''}
    </button>`
}

let lastQueue = ''

function renderQueue() {
  const edit = !!backend
  const shown = edit ? backlog : backlog.slice(0, 8)
  const rest = backlog.length - shown.length
  const last = backlog.length - 1
  const items = shown.map((b, i) => `
    <li class="qcard">
      <span class="qn">${i + 1}</span><span class="qtext">${esc(b)}</span>
      ${i === 0 ? '<span class="qnext">next</span>' : ''}
      ${edit ? `<span class="qtools">
        ${i > 0 ? `<button class="qbtn" data-queue-move="${i}" data-to="${i - 1}" aria-label="Move up: ${esc(b)}">↑</button>` : ''}
        ${i < last ? `<button class="qbtn" data-queue-move="${i}" data-to="${i + 1}" aria-label="Move down: ${esc(b)}">↓</button>` : ''}
        <button class="qbtn remove" data-queue-remove="${i}" aria-label="Remove: ${esc(b)}">×</button>
      </span>` : ''}
    </li>`).join('')
  const html = (items || '<li class="empty">Nothing queued.</li>') + (rest > 0 ? `<li class="queue-more">+${rest} more</li>` : '')
  $('queue-add').hidden = !edit
  $('queue-count').textContent = backlog.length || ''
  if (html !== lastQueue) {
    const focused = document.activeElement?.closest?.('.qbtn') ? document.activeElement.getAttribute('aria-label') : null
    lastQueue = html
    $('queue-list').innerHTML = html
    if (focused) [...document.querySelectorAll('.qbtn')].find((b) => b.getAttribute('aria-label') === focused)?.focus()
  }
}

async function editQueue(body) {
  try {
    await api('POST', 'api/backlog', body)
  } catch (err) {
    $('status').className = 'status error'
    $('status').textContent = `backlog: ${err.message}`
    return
  }
  await poll()
}

function summaryHtml() {
  const count = (pred) => jobs.filter((j) => !j.problem && pred(j)).length
  const running = count((j) => LIVE.has(j.stage))
  const needs = count((j) => j.stage === 'needs-human' || j.stage === 'approved')
  const merged = count((j) => j.stage === 'merged')
  const parts = [`<span><b>${running}</b> running</span>`]
  if (needs) parts.push(`<span class="needs"><b>${needs}</b> need you</span>`)
  parts.push(`<span><b>${merged}</b> merged</span>`)
  if (backlog.length) parts.push(`<span><b>${backlog.length}</b> queued</span>`)
  document.title = needs ? `(${needs}) Software Factory` : 'Software Factory'
  return parts.join('')
}

async function render() {
  const problems = []
  const now = Date.now()
  const byStage = Object.fromEntries(STAGES.map((s) => [s.key, []]))
  const [pasts, decs] = await Promise.all([
    Promise.all(jobs.map((j) => (j.problem ? [] : pastRounds(j)))),
    Promise.all(jobs.map((j) => (j.problem ? {} : decisionOf(j)))),
  ])
  jobs.forEach((job, i) => {
    const stage = job.problem ? 'spec' : COLUMN[job.stage] || job.stage
    if (!byStage[stage]) problems.push(`Job ${job.id} has unknown stage "${job.stage}"`)
    else byStage[stage].push(card(job, pasts[i], decs[i], now))
  })
  const html = STAGES.map((s) => {
    // Newest first within a column.
    let cards = byStage[s.key].reverse()
    let more = ''
    if (s.key === 'merged' && cards.length > MERGED_SHOWN) {
      more = `<button class="show-all" data-toggle-merged>${showAllMerged ? 'Show fewer' : `Show all ${cards.length}`}</button>`
      if (!showAllMerged) cards = cards.slice(0, MERGED_SHOWN)
    }
    return `
    <section class="col ${s.cls || ''}">
      <div class="col-head">
        <h2>${s.label}</h2><span class="who">${esc(whoOf(s))}</span>
        <span class="count">${byStage[s.key].length || ''}</span>
      </div>
      ${cards.join('') || '<div class="empty">—</div>'}${more}
    </section>`
  }).join('')
  const banner = problems.map((p) => `<div class="banner">${esc(p)}</div>`).join('')
  const top = summaryHtml()
  if (html + banner + top !== lastRender) {
    const focused = document.activeElement?.closest?.('[data-job]')?.dataset.job
    lastRender = html + banner + top
    $('board').innerHTML = html
    $('banner').innerHTML = banner
    $('summary').innerHTML = top
    if (focused) document.querySelector(`[data-job="${CSS.escape(focused)}"]`)?.focus()
  }
  renderQueue()
}

async function poll() {
  const status = $('status')
  try {
    ;[jobs, backlog] = await Promise.all([loadJobs(), loadBacklog(), loadBackend()])
    await render()
    if (openJobId) await refreshDrawer()
    status.className = 'status'
    status.innerHTML = `<span class="dot"></span>jobs/ · ${new Date().toLocaleTimeString()}`
  } catch (err) {
    status.className = 'status error'
    status.innerHTML = `<span class="dot"></span>${esc(err.message)}`
  }
}

// python -m http.server serves an HTML index for directories; read its links.
async function listDir(dir) {
  const doc = new DOMParser().parseFromString(await fetchText(dir), 'text/html')
  return [...doc.querySelectorAll('a')].map((a) => decodeURIComponent(a.getAttribute('href')))
}

// Each orchestrator session writes only its own jobs/<id>/job.json. A folder can exist
// for a moment before its job.json does, and a file can be read mid-write.
async function loadJobs() {
  let hrefs
  try {
    hrefs = await listDir('jobs/')
  } catch (err) {
    if (/HTTP 404/.test(err.message)) return []
    throw err
  }
  const ids = hrefs.filter((h) => h.endsWith('/')).map((h) => h.slice(0, -1)).sort()
  return Promise.all(ids.map(async (id) => {
    const text = await fetchOptional(`jobs/${id}/job.json`)
    if (text === null) return { id, problem: 'Starting: no job.json yet.' }
    try {
      const job = JSON.parse(text)
      goodJobs.set(id, job)
      return job
    } catch {
      return goodJobs.get(id) || { id, problem: 'job.json is not valid JSON.' }
    }
  }))
}

async function loadBacklog() {
  const text = await fetchOptional('backlog.md').catch(() => null)
  if (!text) return []
  return text.replace(/\r/g, '').split('\n')
    .map((l) => l.match(/^\s*[-*]\s+\[ \]\s+(.*\S)\s*$/))
    .filter(Boolean)
    .map((m) => m[1])
}

// A plain static server answers 404 here, and the board stays read-only.
async function loadBackend() {
  try {
    const res = await fetch('api/config', { cache: 'no-store' })
    backend = res.ok ? await res.json() : null
  } catch {
    backend = null
  }
  $('open-settings').hidden = !backend
}

// ---- Drawer --------------------------------------------------------------

let lastActions = ''
let acting = false

function actionsHtml(job) {
  if (!backend) return ''
  const buttons = []
  if (MERGEABLE.has(job.stage) && job.stage !== 'approve') {
    const approved = job.stage === 'approved' || decisions.get(job.id)?.verdict === 'APPROVE'
    buttons.push(`<button class="btn primary" data-action="merge" ${acting ? 'disabled' : ''}>${approved ? 'Merge into main' : 'Merge anyway'}</button>`)
  }
  if (!LIVE.has(job.stage)) {
    buttons.push(`<button class="btn danger" data-action="delete" ${acting ? 'disabled' : ''}>Delete job</button>`)
  }
  return buttons.join('')
}

function showMsg(id, text, kind = '') {
  $(id).className = `action-msg ${kind}`
  $(id).textContent = text
}

async function act(action) {
  const job = jobs.find((j) => j.id === openJobId)
  if (!job || acting) return
  const approved = job.stage === 'approved' || decisions.get(job.id)?.verdict === 'APPROVE'
  if (action === 'merge' && !approved &&
      !confirm(`${job.id} was not approved. Merge factory/${job.id} into main anyway?`)) return
  if (action === 'delete' && !confirm(job.stage === 'merged'
    ? `Delete the job folder of ${job.id}? Its work is already in main.`
    : `Delete ${job.id}? This removes its worktree, its branch factory/${job.id} and every commit on it, and its job folder.`)) return
  acting = true
  showMsg('d-msg', action === 'merge' ? 'Merging…' : 'Deleting…')
  lastActions = ''
  try {
    const { message } = await api('POST', `api/jobs/${encodeURIComponent(job.id)}/${action}`, {})
    if (action === 'delete') {
      closeDrawer()
      showMsg('d-msg', '')
    } else {
      showMsg('d-msg', message, 'ok')
    }
  } catch (err) {
    showMsg('d-msg', err.message, 'error')
  } finally {
    acting = false
    lastActions = ''
    await poll()
  }
}

// ---- Settings ------------------------------------------------------------

function openSettings() {
  const { config, agents, models, agentDefaults } = backend
  const form = $('settings-form')
  form.elements.merge.value = config.merge
  form.elements.maxRounds.value = String(config.maxRounds)
  $('s-models').innerHTML = agents.map((a) => `
    <label for="s-model-${a}">${esc(a)}</label>
    <select id="s-model-${a}" data-agent="${esc(a)}">
      <option value="default">agent file (${esc(agentDefaults[a] || 'default')})</option>
      ${models.map((m) => `<option value="${esc(m)}" ${config.models[a] === m ? 'selected' : ''}>${esc(m)}</option>`).join('')}
    </select>`).join('')
  showMsg('s-msg', '')
  $('settings').showModal()
}

async function saveSettings(e) {
  e.preventDefault()
  const form = $('settings-form')
  const models = Object.fromEntries([...form.querySelectorAll('[data-agent]')].map((s) => [s.dataset.agent, s.value]))
  try {
    await api('PUT', 'api/config', { models, merge: form.elements.merge.value, maxRounds: Number(form.elements.maxRounds.value) })
    $('settings').close()
    lastRender = ''
    await poll()
  } catch (err) {
    showMsg('s-msg', err.message, 'error')
  }
}

$('open-settings').addEventListener('click', openSettings)
$('settings-form').addEventListener('submit', saveSettings)
document.querySelector('[data-settings-cancel]').addEventListener('click', () => $('settings').close())
$('queue-add').addEventListener('submit', async (e) => {
  e.preventDefault()
  const input = $('queue-text')
  const text = input.value.replace(/\s+/g, ' ').trim()
  if (!text) return
  input.value = ''
  await editQueue({ action: 'add', text })
  input.focus()
})
// A backlog item is one line, so Enter adds it.
$('queue-text').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    $('queue-add').requestSubmit()
  }
})

async function listFiles(dir) {
  const out = []
  for (const h of await listDir(dir)) {
    if (h.endsWith('/')) out.push(...(await listFiles(dir + h)).map((f) => h + f))
    else if (h.endsWith('.md')) out.push(h)
  }
  return out
}

function fileRank(path) {
  const order = ['spec.md', 'build.md', 'decision.md']
  if (order.includes(path)) return [0, order.indexOf(path), path]
  const round = path.match(/^round-(\d+)\/review-(\w+)\.md$/)
  if (round) return [2, Number(round[1]) * 10 + REVIEWERS.findIndex(([k]) => k === round[2]), path]
  return [1, 0, path] // rework-<k>.md and anything else
}

function sortFiles(files) {
  return files.sort((a, b) => {
    const [x, y] = [fileRank(a), fileRank(b)]
    return x[0] - y[0] || x[1] - y[1] || x[2].localeCompare(y[2])
  })
}

function tone(first) {
  if (/^(VERDICT:\s*PASS|APPROVE|STATUS:\s*READY)$/.test(first)) return 'good'
  if (/^VERDICT:\s*CHANGES$/.test(first)) return 'warn'
  if (/^(ESCALATE|STATUS:\s*BLOCKED)$/.test(first)) return 'stop'
  return null
}

function timelineHtml(job) {
  const h = job.history || []
  return h.map((e, i) => {
    const t = at(e.at)
    const next = i + 1 < h.length ? at(h[i + 1].at) : job.stage === 'merged' || job.stage === 'needs-human' ? NaN : Date.now()
    const label = e.stage === 'review' && e.round ? `review R${e.round}` : e.stage
    const dur = t && next ? ` <span class="t">${ago(next - t)}</span>` : ''
    return `<li title="${esc(new Date(t).toLocaleString())}"><b>${esc(label)}</b>${dur}</li>`
  }).join('')
}

function writeHash() {
  const hash = openJobId ? `#${openJobId}${openPath ? '/' + openPath : ''}` : ''
  if (location.hash !== hash) history.replaceState(null, '', hash || location.pathname + location.search)
}

async function refreshDrawer() {
  const job = jobs.find((j) => j.id === openJobId)
  if (!job || job.problem) return
  $('d-num').textContent = `${job.id} · ${job.stage}${job.round ? ` · round ${job.round}` : ''}`
  $('d-title').textContent = job.feature
  $('d-branch').textContent = [job.branch, job.worktree].filter(Boolean).join(' · ')
  const tl = timelineHtml(job)
  if ($('d-timeline').innerHTML !== tl) $('d-timeline').innerHTML = tl
  const actions = actionsHtml(job)
  if (actions !== lastActions) {
    lastActions = actions
    $('d-actions').innerHTML = actions
  }

  const base = `jobs/${job.id}/`
  const files = sortFiles(await listFiles(base))
  if (!openPath || !files.includes(openPath)) openPath = files[0] || null
  writeHash()

  // Every file's first line drives its dot; build.md and decision.md can change, so read them all.
  const texts = new Map(await Promise.all(files.map(async (f) => [f, await fetchText(base + f)])))
  const text = openPath ? texts.get(openPath) : null

  let group = null
  const list = files.map((f) => {
    const g = f.includes('/') ? f.split('/')[0].replace('round-', 'Round ') : 'Job'
    const head = g !== group ? `<div class="group">${esc(g)}</div>` : ''
    group = g
    const round = f.match(/^round-(\d+)\//)
    const name = round
      ? `<span class="rn">R${round[1]} </span>${esc(f.split('/')[1].replace(/^review-|\.md$/g, ''))}`
      : esc(f)
    const t = tone(texts.get(f).split('\n')[0].trim())
    return `${head}<button class="file ${f === openPath ? 'on' : ''}" data-file="${esc(f)}"
      ${f === openPath ? 'aria-current="true"' : ''}><span class="v ${t || ''}"></span>${name}</button>`
  }).join('') || '<div class="empty">No files yet.</div>'
  if ($('d-files').innerHTML !== list) {
    const focused = document.activeElement?.dataset?.file
    $('d-files').innerHTML = list
    if (focused) document.querySelector(`[data-file="${CSS.escape(focused)}"]`)?.focus()
  }

  if (text !== openText) {
    openText = text
    $('d-doc').innerHTML = text === null ? '<p class="note">The spec-writer hasn’t written anything yet.</p>' : markdown(text)
  }
}

async function openDrawer(id, path = null) {
  if (!jobs.some((j) => j.id === id && !j.problem)) return
  if (!openJobId) opener = document.activeElement
  if (openJobId !== id) showMsg('d-msg', '')
  openJobId = id
  openPath = path
  openText = undefined
  lastActions = ''
  $('overlay').inert = false
  document.body.classList.add('open')
  document.querySelector('.close').focus()
  try {
    await refreshDrawer()
  } catch (err) {
    $('d-doc').innerHTML = `<p class="note">${esc(err.message)}</p>`
  }
}

function closeDrawer() {
  if (!openJobId) return
  const id = openJobId
  openJobId = null
  writeHash()
  $('overlay').inert = true
  document.body.classList.remove('open')
  const back = document.querySelector(`[data-job="${CSS.escape(id)}"]`) || opener
  back?.focus()
  opener = null
}

document.addEventListener('click', (e) => {
  const cardEl = e.target.closest('[data-job]')
  if (cardEl) return openDrawer(cardEl.dataset.job)
  const actionEl = e.target.closest('[data-action]')
  if (actionEl) return act(actionEl.dataset.action)
  const moveEl = e.target.closest('[data-queue-move]')
  if (moveEl) {
    return editQueue({ action: 'move', index: Number(moveEl.dataset.queueMove), to: Number(moveEl.dataset.to) })
  }
  const removeEl = e.target.closest('[data-queue-remove]')
  if (removeEl) return editQueue({ action: 'remove', index: Number(removeEl.dataset.queueRemove) })
  if (e.target.closest('[data-toggle-merged]')) {
    showAllMerged = !showAllMerged
    lastRender = ''
    return render()
  }
  const fileEl = e.target.closest('[data-file]')
  if (fileEl) {
    openPath = fileEl.dataset.file
    openText = undefined
    $('d-doc').parentElement.scrollTop = 0
    return refreshDrawer()
  }
  if (e.target.closest('[data-close]')) closeDrawer()
})
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') return closeDrawer()
  // Keep Tab inside the open drawer.
  if (e.key === 'Tab' && openJobId) {
    const items = [...document.querySelectorAll('.drawer button')].filter((b) => b.offsetParent !== null)
    const [first, last] = [items[0], items.at(-1)]
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
  }
})

function openFromHash() {
  const m = decodeURIComponent(location.hash.slice(1)).match(/^([^/]+)(?:\/(.+))?$/)
  if (m) openDrawer(m[1], m[2] || null)
  else closeDrawer()
}
window.addEventListener('hashchange', openFromHash)

// ---- Markdown (just enough for the factory's files) ---------------------

function inline(s) {
  const codes = []
  // ``a `b` c`` holds backticks; `a` doesn't.
  s = esc(s).replace(/``\s?(.+?)\s?``|`([^`]+)`/g, (_, dbl, one) => `\u0000${codes.push(dbl ?? one) - 1}\u0000`)
  s = s
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\(((?:https?:\/\/|\.{0,2}\/|#)[^)\s]*)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`)
}

function markdown(src) {
  const lines = src.replace(/\r/g, '').split('\n')
  let i = 0
  let html = ''
  const first = (lines[0] || '').trim()
  const t = tone(first)
  if (t) { html += `<div class="verdict ${t}">${esc(first)}</div>`; i = 1 }

  const fence = (l) => l.match(/^(\s*)(```|~~~)/)
  const isList = (l) => /^\s*([-*+]|\d+\.)\s+/.test(l)
  const isRule = (l) => /^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(l)
  const isBlockStart = (l) => /^(#{1,6}\s|\s*>|\|)/.test(l) || fence(l) || isList(l) || isRule(l)

  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { i++; continue }

    const f = fence(line)
    if (f) {
      // Fences inside list items are indented; strip that indent from the body.
      const body = []
      i++
      while (i < lines.length && !lines[i].trimStart().startsWith(f[2])) {
        body.push(lines[i].startsWith(f[1]) ? lines[i].slice(f[1].length) : lines[i].trimStart())
        i++
      }
      i++
      html += `<pre><code>${esc(body.join('\n'))}</code></pre>`
      continue
    }
    if (isRule(line)) { html += '<hr>'; i++; continue }
    const h = line.match(/^(#{1,6})\s+(.*)$/)
    if (h) { const n = Math.min(h[1].length, 4); html += `<h${n}>${inline(h[2])}</h${n}>`; i++; continue }

    if (/^\s*>/.test(line)) {
      const body = []
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ''))
      html += `<blockquote>${inline(body.join(' '))}</blockquote>`
      continue
    }
    if (line.startsWith('|')) {
      const rows = []
      while (i < lines.length && lines[i].startsWith('|')) rows.push(lines[i++])
      const cells = (r) => r.replace(/^\||\|$/g, '').split('|').map((c) => inline(c.trim()))
      const body = rows.filter((r) => !/^\|[\s:|-]+\|$/.test(r))
      html += '<table>' + body.map((r, n) => `<tr>${cells(r).map((c) => n ? `<td>${c}</td>` : `<th>${c}</th>`).join('')}</tr>`).join('') + '</table>'
      continue
    }
    if (isList(line)) {
      const ordered = /^\s*\d+\./.test(line)
      const items = []
      while (i < lines.length && !fence(lines[i]) && (isList(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        const l = lines[i++]
        if (isList(l)) items.push({ indent: l.match(/^\s*/)[0].length, text: l.replace(/^\s*([-*+]|\d+\.)\s+/, '') })
        else items[items.length - 1].text += ' ' + l.trim()
      }
      const lis = items.map(({ indent, text }) => {
        const task = text.match(/^\[([ xX])\]\s+(.*)$/)
        const style = indent ? ` style="margin-left:${indent * 0.6}em"` : ''
        return task
          ? `<li class="task ${task[1] === ' ' ? '' : 'done'}"${style}>${inline(task[2])}</li>`
          : `<li${style}>${inline(text)}</li>`
      }).join('')
      const start = ordered ? Number(line.match(/^\s*(\d+)\./)[1]) : 1
      html += ordered ? `<ol${start !== 1 ? ` start="${start}"` : ''}>${lis}</ol>` : `<ul>${lis}</ul>`
      continue
    }
    const para = []
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) para.push(lines[i++])
    html += `<p>${inline(para.join(' '))}</p>`
  }
  return html
}

poll().then(() => { if (location.hash) openFromHash() })
setInterval(poll, POLL_MS)
