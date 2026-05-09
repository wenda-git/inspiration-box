/**
 * 轻量 Markdown 解析器 · 只认本项目 guide_md 真实会用到的几种语法
 * 返回结构化的"段落数组"，WXML 端 wx:for 渲染。
 *
 * 支持：
 *   ## 二级标题 / ### 三级标题
 *   **加粗**（行内）
 *   - 列表项（行首 - 或 ·）
 *   空行 → 段落分隔
 *
 * 其他原样当正文 P
 */

function parseInline(text) {
  // 把 **加粗** 拆成 [{text, bold}] 数组
  const parts = [];
  const re = /\*\*(.+?)\*\*/g;
  let last = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push({ text: text.slice(last, m.index), bold: false });
    parts.push({ text: m[1], bold: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last), bold: false });
  if (parts.length === 0) parts.push({ text, bold: false });
  return parts;
}

function parseMarkdown(md) {
  if (!md) return [];
  const blocks = [];
  const lines = String(md).replace(/\r\n?/g, '\n').split('\n');

  let curList = null;     // 当前列表项累积
  let curPara = null;     // 当前段落累积

  const flushList = () => {
    if (curList && curList.items.length) blocks.push(curList);
    curList = null;
  };
  const flushPara = () => {
    if (curPara && curPara.spans.length) blocks.push(curPara);
    curPara = null;
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();

    if (line === '') {
      flushList();
      flushPara();
      continue;
    }

    // 标题
    let m = /^(#{2,4})\s+(.+)$/.exec(line);
    if (m) {
      flushList(); flushPara();
      blocks.push({ type: 'h' + m[1].length, spans: parseInline(m[2]) });
      continue;
    }

    // 列表项
    m = /^\s*[-·]\s+(.+)$/.exec(line);
    if (m) {
      flushPara();
      if (!curList) curList = { type: 'ul', items: [] };
      curList.items.push({ spans: parseInline(m[1]) });
      continue;
    }

    // 普通段落
    flushList();
    if (!curPara) curPara = { type: 'p', spans: [] };
    if (curPara.spans.length) curPara.spans.push({ text: ' ', bold: false });
    var inline = parseInline(line.trim());
    for (var i = 0; i < inline.length; i++) curPara.spans.push(inline[i]);
  }

  flushList();
  flushPara();
  return blocks;
}

module.exports = { parseMarkdown };
