/**
 * 极简 Markdown 渲染（聊天气泡用，自研、零依赖、先转义后渲染）。
 *
 * 支持的范围刻意很小 —— 只覆盖模型实际会输出的那些形态：
 * 标题 / 无序列表 / 代码块与行内代码 / 粗体 / 链接 / 分隔线 / 管道表格。
 * 表格是 2026-10-10 补的：模型的行情摘要常带两列表格，此前在聊天气泡里
 * 渲染成一堆竖线（用户反馈"星号表格一大堆符号"）。
 *
 * 安全纪律：整段先 escapeHtml 再做标记替换，链接 href 只放行 http(s) ——
 * v-html 的输入必须是这里产出的、不带原始用户 HTML 的字符串。
 */

const escapeHtml = (text) => text
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#39;')

const renderInline = (text) => text
  .replace(/`([^`\n]+)`/g, '<code>$1</code>')
  .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
  .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')

/** 拆一行表格：`| a | b |` → ['a','b']（首尾竖线可省，单元格内不再转义——进来前已转义过）。 */
const splitTableRow = (line) => line
  .replace(/^\s*\|/, '')
  .replace(/\|\s*$/, '')
  .split('|')
  .map((cell) => cell.trim())

const isTableSeparator = (line) =>
  /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line) && line.includes('-')

export const renderMarkdown = (raw) => {
  if (!raw || typeof raw !== 'string') return ''
  const lines = escapeHtml(raw).split('\n')
  const blocks = []
  let paragraph = []
  let list = null
  let codeLines = null

  const flushParagraph = () => {
    if (paragraph.length === 0) return
    blocks.push(`<p>${paragraph.map(renderInline).join('<br>')}</p>`)
    paragraph = []
  }
  const flushList = () => {
    if (!list) return
    blocks.push(`<ul>${list.join('')}</ul>`)
    list = null
  }
  const flushAll = () => {
    flushParagraph()
    flushList()
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    if (codeLines !== null) {
      if (/^```/.test(line)) {
        blocks.push(`<pre><code>${codeLines.join('\n')}</code></pre>`)
        codeLines = null
      } else {
        codeLines.push(line)
      }
      continue
    }

    if (/^```/.test(line)) {
      flushAll()
      codeLines = []
      continue
    }

    // 分隔线（--- / ***）：模型的回复常拿它分段
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
      flushAll()
      blocks.push('<hr>')
      continue
    }

    // 表格：当前行是表格行、且下一行是分隔行，才开始一张表（避免把零散竖线误判成表）
    if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      flushAll()
      const header = splitTableRow(line)
      const bodyRows = []
      let j = i + 2
      while (j < lines.length && lines[j].includes('|') && lines[j].trim() !== '') {
        bodyRows.push(splitTableRow(lines[j]))
        j++
      }
      const thead = `<thead><tr>${header.map((c) => `<th>${renderInline(c)}</th>`).join('')}</tr></thead>`
      const tbody = `<tbody>${bodyRows
        .map((row) => `<tr>${row.map((c) => `<td>${renderInline(c)}</td>`).join('')}</tr>`)
        .join('')}</tbody>`
      blocks.push(`<div class="md-table-wrap"><table>${thead}${tbody}</table></div>`)
      i = j - 1
      continue
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/)
    if (heading) {
      flushAll()
      const level = heading[1].length
      blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`)
      continue
    }

    const bullet = line.match(/^\s*[-*+]\s+(.+)$/)
    if (bullet) {
      flushParagraph()
      if (!list) list = []
      list.push(`<li>${renderInline(bullet[1])}</li>`)
      continue
    }

    if (line.trim() === '') {
      flushAll()
      continue
    }

    flushList()
    paragraph.push(line)
  }

  if (codeLines !== null) {
    blocks.push(`<pre><code>${codeLines.join('\n')}</code></pre>`)
  }
  flushAll()
  return blocks.join('')
}
