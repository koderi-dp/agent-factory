import { readdir, readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'

const root = fileURLToPath(new URL('../', import.meta.url))
const lock = JSON.parse(
  await readFile(resolve(root, 'package-lock.json'), 'utf8')
)
const supplemental = {
  '@headless-tree/core@1.7.0': 'headless-tree.txt',
  '@headless-tree/react@1.7.0': 'headless-tree.txt',
  'react-remove-scroll-bar@2.3.8': 'react-remove-scroll-bar.txt',
  'victory-vendor@36.9.2': 'victory-vendor.txt',
}
async function licenseFiles(directory) {
  const result = []
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.name === 'node_modules') continue
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) result.push(...(await licenseFiles(path)))
    else if (/^(licen[cs]e|copying|notice|ofl)([.-]|$)/i.test(entry.name))
      result.push(path)
  }
  return result.sort()
}
const sections = [
  'Agent Factory dashboard — third-party notices',
  await readFile(resolve(root, 'src/ui/LICENSE'), 'utf8'),
  await readFile(resolve(root, 'licenses/shadcn-ui.txt'), 'utf8'),
]
for (const [path, entry] of Object.entries(lock.packages).sort(([a], [b]) =>
  a.localeCompare(b)
)) {
  if (!path || entry.dev) continue
  const directory = resolve(root, path)
  try {
    await readdir(directory)
  } catch (error) {
    if (entry.optional && error.code === 'ENOENT') continue
    throw error
  }
  const manifest = JSON.parse(
    await readFile(resolve(directory, 'package.json'), 'utf8')
  )
  const notices = await licenseFiles(directory)
  const name = `${manifest.name}@${manifest.version}`
  const extra = supplemental[name]
  if (extra) notices.push(resolve(root, 'licenses', extra))
  if (!notices.length) throw new Error(`Missing license text for ${name}`)
  sections.push(`\n${name} (${manifest.license})`)
  for (const path of notices) sections.push(await readFile(path, 'utf8'))
}
await writeFile(
  resolve(root, '../src/agent_factory/dashboard_dist/THIRD_PARTY_NOTICES.txt'),
  sections.join('\n\n')
)
