import type { ReactNode } from 'react'

/**
 * Minimal renderer for the formatting the planner actually produces: pipe
 * tables, bullet lists and **bold**. Not a general markdown implementation —
 * just enough that a table of records reads as a table instead of a wall of pipes.
 */

function renderInline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <strong key={index}>{part.slice(2, -2)}</strong>
    ) : (
      <span key={index}>{part}</span>
    ),
  )
}

const isTableRow = (line: string) => line.trim().startsWith('|')
const isSeparatorRow = (line: string) =>
  line.includes('-') && /^\s*\|?[\s:|-]+\|?\s*$/.test(line)
const isBullet = (line: string) => /^\s*[-*•]\s+/.test(line)

const splitCells = (line: string) =>
  line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())

export default function Markdown({ text }: { text: string }) {
  const lines = text.split('\n')
  const blocks: ReactNode[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index]

    if (isTableRow(line)) {
      const rows: string[] = []
      while (index < lines.length && isTableRow(lines[index])) {
        rows.push(lines[index])
        index += 1
      }
      const body = rows.filter((row) => !isSeparatorRow(row))
      const [head, ...rest] = body
      blocks.push(
        <div className="table-scroll" key={`table-${index}`}>
          <table>
            <thead>
              <tr>
                {splitCells(head).map((cell, i) => (
                  <th key={i}>{renderInline(cell)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rest.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {splitCells(row).map((cell, i) => (
                    <td key={i}>{renderInline(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    if (isBullet(line)) {
      const items: string[] = []
      while (index < lines.length && isBullet(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*•]\s+/, ''))
        index += 1
      }
      blocks.push(
        <ul key={`list-${index}`}>
          {items.map((item, i) => (
            <li key={i}>{renderInline(item)}</li>
          ))}
        </ul>,
      )
      continue
    }

    if (line.trim() === '') {
      index += 1
      continue
    }

    const paragraph: string[] = []
    while (
      index < lines.length &&
      lines[index].trim() !== '' &&
      !isTableRow(lines[index]) &&
      !isBullet(lines[index])
    ) {
      paragraph.push(lines[index])
      index += 1
    }
    blocks.push(<p key={`p-${index}`}>{renderInline(paragraph.join('\n'))}</p>)
  }

  return <>{blocks}</>
}
