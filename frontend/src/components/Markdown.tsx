import { type ReactNode } from 'react'

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function renderInline(text: string): string {
  const escaped = escapeHtml(text)
  return escaped
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.+?)`/g, '<code>$1</code>')
}

function renderTable(lines: string[]): ReactNode {
  const rows = lines.map((line) => line.trim()).filter(Boolean)
  if (rows.length < 2) return null
  const cells = rows.map((row) => row.split('|').map((cell) => cell.trim()).filter(Boolean))
  const header = cells[0]
  const body = cells.slice(2)
  return (
    <table className="md-table">
      <thead>
        <tr>{header.map((cell, idx) => <th key={idx} dangerouslySetInnerHTML={{ __html: renderInline(cell) }} />)}</tr>
      </thead>
      <tbody>
        {body.map((row, rowIndex) => (
          <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex} dangerouslySetInnerHTML={{ __html: renderInline(cell) }} />)}</tr>
        ))}
      </tbody>
    </table>
  )
}

function renderCodeBlock(language: string, code: string): ReactNode {
  return (
    <figure className="md-codeblock">
      <figcaption className="md-codeblock-head">
        <span>{language || 'text'}</span>
      </figcaption>
      <pre className="md-codeblock-pre"><code>{code}</code></pre>
    </figure>
  )
}

export function Markdown({ content }: { content: string }) {
  const parts: ReactNode[] = []
  const lines = content.split(/\r?\n/)
  let paragraph: string[] = []
  let list: string[] = []
  let table: string[] = []
  let code: string[] = []
  let codeLang = ''
  let inCode = false

  const flush = () => {
    if (inCode) return
    if (table.length >= 2) {
      parts.push(<div key={`t-${parts.length}`}>{renderTable(table)}</div>)
      table = []
      return
    }
    if (list.length) {
      parts.push(<ul key={`l-${parts.length}`}>{list.map((item, idx) => <li key={idx} dangerouslySetInnerHTML={{ __html: renderInline(item.replace(/^[-*]\s+/, '')) }} />)}</ul>)
      list = []
    }
    if (paragraph.length) {
      parts.push(<p key={`p-${parts.length}`} dangerouslySetInnerHTML={{ __html: renderInline(paragraph.join(' ')) }} />)
      paragraph = []
    }
  }

  for (const line of lines) {
    const trimmed = line.trimEnd()
    const fence = trimmed.match(/^```\s*([^`]*)\s*$/)
    if (fence) {
      if (inCode) {
        parts.push(<div key={`c-${parts.length}`}>{renderCodeBlock(codeLang, code.join('\n'))}</div>)
        code = []
        codeLang = ''
        inCode = false
      } else {
        flush()
        inCode = true
        codeLang = fence[1].trim()
      }
      continue
    }
    if (inCode) {
      code.push(line)
      continue
    }
    const trimmedLine = line.trim()
    if (!trimmedLine) {
      flush()
      continue
    }
    if (trimmedLine.includes('|') && !trimmedLine.startsWith('```')) {
      table.push(trimmedLine)
      continue
    }
    if (/^[-*]\s+/.test(trimmedLine)) {
      if (paragraph.length) flush()
      list.push(trimmedLine)
      continue
    }
    if (list.length || table.length) flush()
    paragraph.push(trimmedLine)
  }
  flush()
  return <>{parts}</>
}
