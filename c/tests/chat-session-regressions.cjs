const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
const path = require('node:path')
const root = path.resolve(__dirname, '..')
const source = fs.readFileSync(path.join(root, 'src/hooks/useChatSessions.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const key = 'yamibo.chat.selected-session.v3'
async function scenario(saved, pages, preferred = '') {
  const values = [], effects = [], storage = new Map([[key, JSON.stringify(saved)]])
  let cursor = 0
  const requests = []
  const hooks = {
    useState(initial) { const i = cursor++; if (!(i in values)) values[i] = typeof initial === 'function' ? initial() : initial; return [values[i], next => { values[i] = typeof next === 'function' ? next(values[i]) : next }] },
    useRef(initial) { const i = cursor++; return values[i] ||= { current: initial } },
    useCallback(fn) { return fn },
    useEffect(fn) { effects.push(fn) },
  }
  const api = { chatContext: async () => ({}), chatSessions: async ({offset}) => { requests.push(offset); return pages[offset] || { sessions: [], has_more: false } }, chatMessages: async id => [{ id, content: id }], deleteChatSession: async () => {} }
  const exports = {}
  vm.runInNewContext(compiled, { exports, require: name => name === 'react' ? hooks : { api }, localStorage: { getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v) }, AbortController })
  const render = () => { cursor = 0; effects.length = 0; return exports.useChatSessions(preferred) }
  let result = render()
  effects.forEach(effect => effect())
  await new Promise(resolve => setImmediate(resolve))
  result = render()
  // Update refs as React's post-render effects do, without re-running the mount effect.
  effects.slice(0, 2).forEach(effect => effect())
  return { result, render, storage, requests }
}
;(async () => {
  const older = await scenario('older', { 0: { sessions: [{ id: 'new' }], has_more: true }, 1: { sessions: [{ id: 'older' }], has_more: false } })
  assert.equal(older.result.selected, 'older'); assert.deepEqual(older.requests, [0,1]); assert.equal(older.result.messages[0].id, 'older')
  const missing = await scenario('deleted', { 0: { sessions: [{ id: 'new' }], has_more: false } })
  assert.equal(missing.result.selected, 'new'); assert.equal(JSON.parse(missing.storage.get(key)), 'new')
  const empty = await scenario('deleted', {})
  assert.equal(empty.result.selected, ''); assert.equal(JSON.parse(empty.storage.get(key)), '')
  const explicit = await scenario('other', { 0: { sessions: [{ id: 'linked' }], has_more: false } }, 'linked')
  assert.equal(explicit.result.selected, 'linked'); assert.equal(JSON.parse(explicit.storage.get(key)), 'linked')
  await explicit.result.remove('linked')
  assert.equal(explicit.render().selected, ''); assert.equal(JSON.parse(explicit.storage.get(key)), '')
  const rag = fs.readFileSync(path.join(root, 'src/pages/Rag.tsx'), 'utf8')
  const validation = rag.match(/if \(tid && \(([^\n]+)\)\) \{/)[1]
  const invalid = new Function('tid', `return ${validation}`)
  for (const tid of ['572313', '1']) assert.equal(invalid(tid), false)
  for (const tid of ['0','-1','1.5','abc','9007199254740993']) assert.equal(invalid(tid), true)
  console.log('PASS: session JSON restore, later-page restore, missing/empty selection, linked session, last deletion, TID validation')
})().catch(error => { console.error(error); process.exitCode = 1 })
