(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const { useEffect, useMemo, useState, useCallback } = SDK.hooks;
  const h = React.createElement;
  const isoTimeAgo = (SDK.utils && SDK.utils.isoTimeAgo) || null;

  const API = "/api/plugins/hermes-lcm";

  function short(s, n) {
    const text = String(s || "");
    return text.length > n ? text.slice(0, n - 1) + "…" : text;
  }

  function fmtInt(n) {
    const v = Number(n) || 0;
    return v.toLocaleString();
  }

  function fmtTime(epoch) {
    const v = Number(epoch);
    if (!v) return "";
    try {
      const d = new Date(v * 1000);
      if (isoTimeAgo) return isoTimeAgo(d.toISOString());
      return d.toLocaleString();
    } catch (e) {
      return String(epoch);
    }
  }

  // Flatten markdown to readable plain text (for compact list previews/titles).
  function stripMd(s) {
    return String(s == null ? "" : s)
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/\*([^*]+)\*/g, "$1")
      .replace(/^#{1,6}\s+/gm, "")
      .replace(/^\s*>\s?/gm, "")
      .replace(/^\s*[-*+]\s+/gm, "")
      .replace(/\[([^\]]+)\]\([^)\s]+\)/g, "$1")
      .replace(/\s+/g, " ")
      .trim();
  }

  // Derive a short title from a summary: first heading, else first bold run,
  // else the first sentence/line of the flattened text.
  function summaryTitle(s) {
    const txt = String(s == null ? "" : s);
    const hd = txt.match(/^\s*#{1,6}\s+(.+?)\s*$/m);
    if (hd) return stripMd(hd[1]);
    const bold = txt.match(/\*\*([^*]+)\*\*/);
    if (bold) return stripMd(bold[1]);
    const flat = stripMd(txt);
    const dot = flat.search(/[.!?](\s|$)/);
    return dot > 12 && dot < 90 ? flat.slice(0, dot + 1) : flat;
  }

  // Pretty session label from an id like "20260529_011608_ab12cd".
  function sessionLabel(id) {
    const txt = String(id == null ? "" : id);
    const m = txt.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/);
    if (m) return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}`;
    return short(txt, 36);
  }

  function sessionTail(id) {
    const txt = String(id == null ? "" : id);
    const m = txt.match(/_([0-9a-f]{4,})$/i);
    return m ? m[1] : "";
  }

  // --- snippet rendering: backend wraps highlights in [ ... ] ----------------
  function renderSnippet(text) {
    const raw = String(text || "");
    const parts = [];
    const re = /\[([^\]]*)\]/g;
    let last = 0;
    let m;
    let i = 0;
    while ((m = re.exec(raw)) !== null) {
      if (m.index > last) parts.push(raw.slice(last, m.index));
      parts.push(h("mark", { key: "mk" + i++, className: "hermes-lcm-mark" }, m[1]));
      last = re.lastIndex;
    }
    if (last < raw.length) parts.push(raw.slice(last));
    return parts.length ? parts : raw;
  }

  // --- minimal self-contained markdown -> React. XSS-safe: builds elements,
  // never uses innerHTML. Underscores are left literal so snake_case and paths
  // (kanban_block, auto_model_routing) are not mangled into emphasis. ---------
  function mdInlineNodes(text, kp) {
    const nodes = [];
    const codeRe = /`([^`]+)`/g;
    let last = 0, m, i = 0;
    while ((m = codeRe.exec(text)) !== null) {
      if (m.index > last) mdEmphasis(text.slice(last, m.index), nodes, kp + "t" + i);
      nodes.push(h("code", { key: kp + "c" + i, className: "hermes-lcm-md-code" }, m[1]));
      last = codeRe.lastIndex; i++;
    }
    if (last < text.length) mdEmphasis(text.slice(last), nodes, kp + "t" + i);
    return nodes;
  }

  function mdEmphasis(str, nodes, kp) {
    const re = /(\*\*)([\s\S]+?)\*\*|(\*)([^*\n]+?)\*|\[([^\]]+)\]\(([^)\s]+)\)/;
    let rest = str, i = 0, m;
    while ((m = re.exec(rest)) !== null) {
      if (m.index > 0) nodes.push(rest.slice(0, m.index));
      if (m[1]) nodes.push(h("strong", { key: kp + "b" + i }, mdInlineNodes(m[2], kp + "b" + i + "-")));
      else if (m[3]) nodes.push(h("em", { key: kp + "e" + i }, mdInlineNodes(m[4], kp + "e" + i + "-")));
      else nodes.push(h("a", {
        key: kp + "a" + i, href: m[6], target: "_blank", rel: "noopener noreferrer",
        className: "hermes-lcm-md-link",
      }, m[5]));
      rest = rest.slice(m.index + m[0].length); i++;
    }
    if (rest) nodes.push(rest);
  }

  function mdBuildList(items, kp) {
    const base = items[0].indent;
    const ordered = items[0].ordered;
    const children = [];
    let i = 0, li = 0;
    while (i < items.length) {
      if (items[i].indent > base) {
        const start = i;
        while (i < items.length && items[i].indent > base) i++;
        const nested = mdBuildList(items.slice(start), kp + "n" + li);
        if (children.length) {
          const prev = children[children.length - 1];
          children[children.length - 1] = h("li", { key: prev.key },
            [].concat(prev.props.children, nested));
        } else {
          children.push(h("li", { key: kp + "li" + li++ }, nested));
        }
        continue;
      }
      children.push(h("li", { key: kp + "li" + li++ }, mdInlineNodes(items[i].text, kp + "x" + li)));
      i++;
    }
    return h(ordered ? "ol" : "ul", { key: kp, className: "hermes-lcm-md-list" }, children);
  }

  function mdToReact(src) {
    const lines = String(src == null ? "" : src).replace(/\r\n?/g, "\n").split("\n");
    const blocks = [];
    let i = 0, key = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (/^\s*```/.test(line)) {
        const buf = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++;
        blocks.push(h("pre", { key: "p" + key++, className: "hermes-lcm-md-pre" },
          h("code", null, buf.join("\n"))));
        continue;
      }
      if (/^\s*$/.test(line)) { i++; continue; }
      const hd = line.match(/^(#{1,6})\s+(.*)$/);
      if (hd) {
        blocks.push(h("div", {
          key: "p" + key++,
          className: "hermes-lcm-md-h hermes-lcm-md-h" + hd[1].length,
        }, mdInlineNodes(hd[2], "h" + key)));
        i++; continue;
      }
      if (/^\s*>\s?/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          buf.push(lines[i].replace(/^\s*>\s?/, "")); i++;
        }
        blocks.push(h("blockquote", { key: "p" + key++, className: "hermes-lcm-md-quote" },
          mdInlineNodes(buf.join(" "), "q" + key)));
        continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
          const mm = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
          items.push({ indent: mm[1].length, ordered: /\d/.test(mm[2]), text: mm[3] });
          i++;
        }
        blocks.push(mdBuildList(items, "l" + key++));
        continue;
      }
      const buf = [];
      while (i < lines.length && !/^\s*$/.test(lines[i])
        && !/^\s*```/.test(lines[i])
        && !/^(#{1,6})\s+/.test(lines[i])
        && !/^\s*>\s?/.test(lines[i])
        && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
        buf.push(lines[i]); i++;
      }
      const kids = [];
      buf.forEach(function (ln, idx) {
        if (idx) kids.push(h("br", { key: "br" + idx }));
        const sub = mdInlineNodes(ln, "p" + key + "-" + idx);
        for (let s = 0; s < sub.length; s++) kids.push(sub[s]);
      });
      blocks.push(h("p", { key: "p" + key++, className: "hermes-lcm-md-p" }, kids));
    }
    return blocks;
  }

  function MarkdownText(props) {
    const text = String(props.text == null ? "" : props.text);
    let nodes;
    try { nodes = mdToReact(text); } catch (e) { nodes = [text]; }
    return h("div", {
      className: "hermes-lcm-md" + (props.className ? " " + props.className : ""),
    }, nodes);
  }

  function BarList(props) {
    const rows = props.rows || [];
    const keyName = props.keyName;
    const onPick = props.onPick;
    const total = rows.reduce((acc, row) => acc + (Number(row.count) || 0), 0) || 1;
    if (!rows.length) return h("div", { className: "hermes-lcm-empty" }, "No data");
    return h("div", { className: "hermes-lcm-bars" }, rows.map(function (row, idx) {
      const label = String(row[keyName] == null ? "(none)" : row[keyName]);
      const count = Number(row.count) || 0;
      const pct = Math.max(2, Math.round((count / total) * 100));
      const clickable = typeof onPick === "function";
      return h("div", {
        key: label + ":" + idx,
        className: "hermes-lcm-bar-row" + (clickable ? " hermes-lcm-clk" : ""),
        onClick: clickable ? function () { onPick(label); } : undefined,
      }, [
        h("div", { className: "hermes-lcm-bar-head" }, [
          h("span", { className: "hermes-lcm-k" }, label),
          h("span", { className: "hermes-lcm-v" }, fmtInt(count)),
        ]),
        h("div", { className: "hermes-lcm-bar-track" }, [
          h("div", { className: "hermes-lcm-bar-fill", style: { width: pct + "%" } }),
        ]),
      ]);
    }));
  }

  // --- inline SVG: message-volume timeline ----------------------------------
  // Responsive CSS bar chart (no SVG stretching, so bars stay crisp and the
  // summary markers render as true round dots regardless of bucket count).
  function TimelineChart(props) {
    const buckets = props.buckets || [];
    const nodeBuckets = props.nodeBuckets || [];
    if (!buckets.length) return h("div", { className: "hermes-lcm-empty" }, "No timeline data");
    const maxCount = buckets.reduce((acc, b) => Math.max(acc, Number(b.count) || 0), 0) || 1;
    const nodeByBucket = {};
    nodeBuckets.forEach(function (nb) { nodeByBucket[nb.bucket] = Number(nb.count) || 0; });

    const cols = buckets.map(function (b, i) {
      const count = Number(b.count) || 0;
      const pct = count > 0 ? Math.max(3, Math.round((count / maxCount) * 100)) : 0;
      const nodes = nodeByBucket[b.bucket] || 0;
      const tip = `${b.bucket}: ${fmtInt(count)} messages`
        + (nodes ? ` · ${fmtInt(nodes)} summaries` : "");
      return h("div", { key: b.bucket + i, className: "hermes-lcm-tl-col", title: tip }, [
        h("div", { className: "hermes-lcm-tl-dot" + (nodes ? " hermes-lcm-tl-dot-on" : "") }),
        h("div", { className: "hermes-lcm-tl-bar", style: { height: pct + "%" } }),
      ]);
    });

    return h("div", { className: "hermes-lcm-tl" }, [
      h("div", { className: "hermes-lcm-tl-bars" }, cols),
      h("div", { className: "hermes-lcm-svg-axis" }, [
        h("span", null, short(buckets[0].bucket, 16)),
        h("span", null, short(buckets[buckets.length - 1].bucket, 16)),
      ]),
    ]);
  }

  // --- inline SVG: per-group compression (kept vs saved) --------------------
  function CompressionBars(props) {
    const groups = props.groups || [];
    const onPick = props.onPick;
    if (!groups.length) return h("div", { className: "hermes-lcm-empty" }, "No compression data");
    const maxSrc = groups.reduce((acc, g) => Math.max(acc, Number(g.source_token_count) || 0), 0) || 1;
    return h("div", { className: "hermes-lcm-comp" }, groups.map(function (g, idx) {
      const src = Number(g.source_token_count) || 0;
      const out = Number(g.token_count) || 0;
      const totalW = Math.max(0.5, (src / maxSrc) * 100);
      const keptW = src > 0 ? (out / src) * totalW : 0;
      const sid = g.session_id != null ? g.session_id : g.key;
      const label = (typeof sid === "string" && /^\d{8}_/.test(sid))
        ? sessionLabel(sid)
        : (g.depth != null ? `node #${g.key} (D${g.depth})` : String(g.key));
      const clickable = typeof onPick === "function";
      return h("div", {
        key: String(g.key) + idx,
        className: "hermes-lcm-comp-row" + (clickable ? " hermes-lcm-clk" : ""),
        onClick: clickable ? function () { onPick(g); } : undefined,
      }, [
        h("div", { className: "hermes-lcm-comp-head" }, [
          h("span", { className: "hermes-lcm-k" }, label),
          h("span", { className: "hermes-lcm-v" }, `${g.ratio || 0}× · ${fmtInt(src)}→${fmtInt(out)}`),
        ]),
        h("svg", {
          viewBox: "0 0 100 8", preserveAspectRatio: "none",
          width: "100%", height: 8, className: "hermes-lcm-svgbar",
        }, [
          h("rect", { x: 0, y: 0, width: totalW, height: 8, rx: 1.5, className: "hermes-lcm-svg-saved" }),
          h("rect", { x: 0, y: 0, width: keptW, height: 8, rx: 1.5, className: "hermes-lcm-svg-kept" }),
        ]),
      ]);
    }));
  }

  function Stat(props) {
    return h("div", { className: "hermes-lcm-stat" }, [
      h("div", { className: "hermes-lcm-stat-v" }, props.value),
      h("div", { className: "hermes-lcm-stat-k" }, props.label),
    ]);
  }

  // --- pretty tool-result rendering. Known tools get bespoke components; any
  // other JSON falls back to a clean key/value view; non-JSON to markdown. ----
  // Tool results are often a JSON value followed by a trailing human note
  // (e.g. `{...}\n[use offset=120 to see more]`), so strict JSON.parse fails.
  // Extract the leading {...}/[...] by brace-matching, keep the rest as a note.
  function parseLeadingJSON(s) {
    if (typeof s !== "string") return null;
    let i = 0;
    while (i < s.length && /\s/.test(s[i])) i++;
    const open = s[i];
    if (open !== "{" && open !== "[") {
      try { return { value: JSON.parse(s), rest: "" }; } catch (e) { return null; }
    }
    const close = open === "{" ? "}" : "]";
    let depth = 0, inStr = false, esc = false, end = -1;
    for (let j = i; j < s.length; j++) {
      const ch = s[j];
      if (inStr) {
        if (esc) esc = false;
        else if (ch === "\\") esc = true;
        else if (ch === '"') inStr = false;
        continue;
      }
      if (ch === '"') inStr = true;
      else if (ch === open) depth++;
      else if (ch === close) { depth--; if (depth === 0) { end = j + 1; break; } }
    }
    if (end === -1) return null;
    try { return { value: JSON.parse(s.slice(i, end)), rest: s.slice(end).trim() }; }
    catch (e) { return null; }
  }

  function clampText(s, n) {
    const t = String(s == null ? "" : s);
    return t.length > n ? t.slice(0, n) + "\n…(" + fmtInt(t.length - n) + " more chars)" : t;
  }

  function codeBlock(text) {
    return h("pre", { className: "hermes-lcm-md-pre" }, h("code", null, clampText(text, 4000)));
  }

  function toolBadge(label, kind) {
    return h("span", { className: "hermes-lcm-tag" + (kind ? " hermes-lcm-tag-" + kind : "") }, label);
  }

  function ToolOutput(d) {
    const out = d.output != null
      ? d.output
      : (typeof d.result === "string" ? d.result
        : (d.result != null ? JSON.stringify(d.result, null, 2) : ""));
    const code = d.exit_code != null ? d.exit_code : d.status;
    const ok = d.exit_code === 0 || d.status === "success" || d.status === "exited" || d.success === true;
    return h("div", { className: "hermes-lcm-tool" }, [
      (code != null || d.duration_seconds != null) ? h("div", { className: "hermes-lcm-tool-meta" }, [
        code != null ? toolBadge((d.exit_code != null ? "exit " : "") + code, ok ? "ok" : "bad") : null,
        d.duration_seconds != null ? h("span", { className: "hermes-lcm-dim" }, d.duration_seconds + "s") : null,
      ]) : null,
      out ? codeBlock(out) : null,
      d.error ? h("div", { className: "hermes-lcm-tool-err" }, String(d.error)) : null,
      d.timeout_note ? h("div", { className: "hermes-lcm-tool-err" }, String(d.timeout_note)) : null,
    ]);
  }

  function ToolReadFile(d) {
    return h("div", { className: "hermes-lcm-tool" }, [
      h("div", { className: "hermes-lcm-tool-meta" }, [
        d.total_lines != null ? toolBadge(fmtInt(d.total_lines) + " lines") : null,
        d.file_size != null ? toolBadge(fmtInt(d.file_size) + " B") : null,
        d.truncated ? toolBadge("truncated", "warn") : null,
        d.is_image ? toolBadge("image") : null,
      ]),
      d.content ? codeBlock(d.content) : null,
      (d.hint || d._hint) ? h("div", { className: "hermes-lcm-dim" }, String(d.hint || d._hint)) : null,
    ]);
  }

  function ToolSearchFiles(d) {
    const matches = d.matches || [];
    return h("div", { className: "hermes-lcm-tool" }, [
      h("div", { className: "hermes-lcm-tool-meta" }, [
        toolBadge(fmtInt(d.total_count != null ? d.total_count : matches.length) + " matches"),
      ]),
      h("div", { className: "hermes-lcm-tool-matches" }, matches.slice(0, 50).map(function (mm, i) {
        return h("div", { key: i, className: "hermes-lcm-tool-match" }, [
          h("div", { className: "hermes-lcm-tool-match-loc" }, [
            h("span", { className: "hermes-lcm-tool-path" }, short(String(mm.path || ""), 72)),
            mm.line != null ? h("span", { className: "hermes-lcm-dim" }, ":" + mm.line) : null,
          ]),
          mm.content != null
            ? h("code", { className: "hermes-lcm-tool-match-code" }, short(String(mm.content), 220))
            : null,
        ]);
      })),
      matches.length > 50 ? h("div", { className: "hermes-lcm-dim" }, "+" + fmtInt(matches.length - 50) + " more") : null,
    ]);
  }

  const TODO_ICON = { completed: "✓", in_progress: "◐", pending: "○", cancelled: "✗" };
  function ToolTodo(d) {
    const todos = d.todos || [];
    const s = d.summary || {};
    return h("div", { className: "hermes-lcm-tool" }, [
      h("div", { className: "hermes-lcm-tool-meta" }, [
        s.completed != null ? toolBadge(s.completed + " done", "ok") : null,
        s.in_progress != null ? toolBadge(s.in_progress + " active") : null,
        s.pending != null ? toolBadge(s.pending + " todo") : null,
        s.cancelled ? toolBadge(s.cancelled + " cancelled") : null,
      ]),
      h("ul", { className: "hermes-lcm-todo" }, todos.map(function (t, i) {
        const st = String(t.status || "pending");
        return h("li", { key: t.id || i, className: "hermes-lcm-todo-item hermes-lcm-todo-" + st }, [
          h("span", { className: "hermes-lcm-todo-ic" }, TODO_ICON[st] || "•"),
          h("span", null, String(t.content || "")),
        ]);
      })),
    ]);
  }

  function ToolPatch(d, raw) {
    let diff = d && d.diff;
    if (diff == null && typeof raw === "string") {
      const mm = raw.match(/"diff"\s*:\s*"([\s\S]*?)"\s*\}?\s*$/);
      diff = mm ? mm[1].replace(/\\n/g, "\n").replace(/\\"/g, '"').replace(/\\t/g, "\t") : raw;
    }
    const lines = String(diff || "").split("\n");
    return h("div", { className: "hermes-lcm-tool" }, [
      (d && d.success != null) ? h("div", { className: "hermes-lcm-tool-meta" }, [
        toolBadge(d.success ? "applied" : "failed", d.success ? "ok" : "bad"),
      ]) : null,
      h("pre", { className: "hermes-lcm-md-pre hermes-lcm-diff" }, lines.slice(0, 240).map(function (ln, i) {
        const c0 = ln.charAt(0);
        const cls = c0 === "+" ? "hermes-lcm-diff-add"
          : c0 === "-" ? "hermes-lcm-diff-del"
          : c0 === "@" ? "hermes-lcm-diff-hunk" : "";
        return h("div", { key: i, className: cls }, ln || " ");
      })),
    ]);
  }

  function ToolSkill(d) {
    const tags = d.tags || [];
    return h("div", { className: "hermes-lcm-tool" }, [
      h("div", { className: "hermes-lcm-tool-meta" }, [
        d.name ? toolBadge(d.name) : null,
        tags.slice(0, 6).map(function (t, i) { return h("span", { key: i, className: "hermes-lcm-dim" }, "#" + t); }),
      ]),
      d.description ? h("div", { className: "hermes-lcm-dim" }, String(d.description)) : null,
      d.content ? h(MarkdownText, { text: clampText(d.content, 4000) }) : null,
    ]);
  }

  function ToolGeneric(d) {
    if (Array.isArray(d)) return codeBlock(JSON.stringify(d, null, 2));
    return h("div", { className: "hermes-lcm-kv" }, Object.keys(d).map(function (k, i) {
      const v = d[k];
      let vn;
      if (v == null) vn = h("span", { className: "hermes-lcm-dim" }, "null");
      else if (typeof v === "object") {
        vn = h("pre", { className: "hermes-lcm-md-pre" },
          h("code", null, clampText(JSON.stringify(v, null, 2), 1500)));
      } else vn = h("span", null, String(v));
      return h("div", { key: k + i, className: "hermes-lcm-kv-row" }, [
        h("span", { className: "hermes-lcm-kv-k" }, k),
        h("span", { className: "hermes-lcm-kv-v" }, vn),
      ]);
    }));
  }

  function ToolResult(props) {
    const name = String(props.name || "");
    const raw = props.content;
    const parsed = parseLeadingJSON(raw);
    const data = parsed ? parsed.value : undefined;
    const note = (parsed && parsed.rest) ? parsed.rest : "";
    let body = null;
    try {
      if ((name === "terminal" || name === "process" || name === "execute_code" || name === "shell")
        && data && typeof data === "object") body = ToolOutput(data);
      else if (name === "read_file" && data) body = ToolReadFile(data);
      else if (name === "search_files" && data) body = ToolSearchFiles(data);
      else if (name === "todo" && data) body = ToolTodo(data);
      else if (name === "patch") body = ToolPatch(data, raw);
      else if (name === "skill_view" && data) body = ToolSkill(data);
      else if (data && typeof data === "object") body = ToolGeneric(data);
    } catch (e) { body = null; }
    if (body == null) {
      return h(MarkdownText, { className: "hermes-lcm-msg-body", text: short(String(raw == null ? "" : raw), 4000) });
    }
    if (note) {
      return h("div", { className: "hermes-lcm-tool" }, [
        body,
        h("div", { className: "hermes-lcm-dim hermes-lcm-tool-note" }, short(note, 400)),
      ]);
    }
    return body;
  }

  // --- detail drawer (session / node) ---------------------------------------
  function MessageItem(props) {
    const m = props.m;
    let body;
    if (m.snippet) {
      body = h("div", { className: "hermes-lcm-msg-body" }, renderSnippet(m.snippet));
    } else if (m.role === "tool") {
      body = h(ToolResult, { name: m.tool_name, content: m.content });
    } else {
      body = h(MarkdownText, { className: "hermes-lcm-msg-body", text: short(m.content, 4000) });
    }
    return h("div", { className: "hermes-lcm-msg" }, [
      h("div", { className: "hermes-lcm-msg-meta" }, [
        h("span", { className: "hermes-lcm-tag" }, m.role || "?"),
        m.source ? h("span", { className: "hermes-lcm-tag hermes-lcm-tag-src" }, m.source) : null,
        m.tool_name ? h("span", { className: "hermes-lcm-tag" }, m.tool_name) : null,
        h("span", { className: "hermes-lcm-dim" }, fmtTime(m.timestamp)),
        m.token_estimate ? h("span", { className: "hermes-lcm-dim" }, `${fmtInt(m.token_estimate)} tok`) : null,
      ]),
      body,
    ]);
  }

  function NodeRef(props) {
    const n = props.n;
    const onOpen = props.onOpen;
    return h("div", {
      className: "hermes-lcm-noderef hermes-lcm-clk",
      onClick: function () { onOpen(n.node_id); },
    }, [
      h("div", { className: "hermes-lcm-msg-meta" }, [
        h("span", { className: "hermes-lcm-tag" }, `D${n.depth}`),
        n.category ? h("span", { className: "hermes-lcm-tag" }, n.category) : null,
        h("span", { className: "hermes-lcm-dim" }, `#${n.node_id}`),
        n.source_type ? h("span", { className: "hermes-lcm-dim" }, n.source_type) : null,
        (n.token_count != null) ? h("span", { className: "hermes-lcm-dim" }, `${fmtInt(n.token_count)} tok`) : null,
      ]),
      h("div", { className: "hermes-lcm-msg-body" }, short(n.summary, 400)),
    ]);
  }

  function NodeDetail(props) {
    const d = props.data;
    const node = d.node;
    const onOpenNode = props.onOpenNode;
    const onOpenSession = props.onOpenSession;
    if (!node) return h("div", { className: "hermes-lcm-empty" }, "Node not found");
    const sources = d.sources || {};
    return h("div", { className: "hermes-lcm-detail" }, [
      h("div", { className: "hermes-lcm-detail-meta" }, [
        h("span", { className: "hermes-lcm-tag" }, `Depth ${node.depth}`),
        node.category ? h("span", { className: "hermes-lcm-tag" }, node.category) : null,
        h("span", {
          className: "hermes-lcm-tag hermes-lcm-clk",
          onClick: function () { onOpenSession(node.session_id); },
        }, short(node.session_id, 28)),
        h("span", { className: "hermes-lcm-dim" },
          `${fmtInt(node.source_token_count)}→${fmtInt(node.token_count)} tok`),
      ]),
      h("h4", null, "Summary"),
      h(MarkdownText, { className: "hermes-lcm-summary", text: node.summary }),
      node.expand_hint ? h("div", { className: "hermes-lcm-hint" }, [
        h("strong", null, "Expand hint: "), node.expand_hint,
      ]) : null,
      h("h4", null, `Sources (${sources.type || "?"}, ${(sources.ids || []).length})`),
      (function () {
        const isNodes = sources.type === "nodes";
        const items = isNodes ? (sources.nodes || []) : (sources.messages || []);
        if (!items.length) {
          return h("div", { className: "hermes-lcm-empty" },
            (sources.ids || []).length
              ? "Source items are no longer in the database."
              : "This summary records no source items.");
        }
        return h("div", { className: "hermes-lcm-stream" }, items.map(function (it) {
          return isNodes
            ? h(NodeRef, { key: it.node_id, n: it, onOpen: onOpenNode })
            : h(MessageItem, { key: it.store_id, m: it });
        }));
      })(),
    ]);
  }

  function SessionDetail(props) {
    const d = props.data;
    const onOpenNode = props.onOpenNode;
    const c = d.counts || {};
    return h("div", { className: "hermes-lcm-detail" }, [
      h("div", { className: "hermes-lcm-statrow" }, [
        h(Stat, { value: fmtInt(c.message_count), label: "messages" }),
        h(Stat, { value: fmtInt(c.summary_node_count), label: "summaries" }),
        h(Stat, { value: fmtInt(c.token_estimate_total), label: "msg tokens" }),
        h(Stat, {
          value: ratioStr(c.source_token_count, c.summary_token_count),
          label: "compression",
        }),
      ]),
      (d.summary_nodes && d.summary_nodes.length) ? h("div", null, [
        h("h4", null, `Summary nodes (${d.summary_nodes.length})`),
        h("div", { className: "hermes-lcm-stream" }, d.summary_nodes.map(function (n) {
          return h(NodeRef, { key: n.node_id, n: n, onOpen: onOpenNode });
        })),
      ]) : null,
      h("h4", null, `Messages (${(d.messages || []).length}${d.has_more ? "+" : ""})`),
      h("div", { className: "hermes-lcm-stream" }, (d.messages || []).map(function (m) {
        return h(MessageItem, { key: m.store_id, m: m });
      })),
    ]);
  }

  function ratioStr(src, out) {
    const s = Number(src) || 0;
    const o = Number(out) || 0;
    if (!o) return "—";
    return (Math.round((s / o) * 10) / 10) + "×";
  }

  function Drawer(props) {
    if (!props.open) return null;
    return h("div", { className: "hermes-lcm-drawer-overlay", onClick: props.onClose }, [
      h("div", {
        className: "hermes-lcm-drawer",
        onClick: function (e) { e.stopPropagation(); },
      }, [
        h("div", { className: "hermes-lcm-drawer-head" }, [
          props.canBack ? h("button", {
            className: "hermes-lcm-btn", onClick: props.onBack,
          }, "← Back") : null,
          h("div", { className: "hermes-lcm-drawer-title" }, props.title),
          h("button", { className: "hermes-lcm-btn", onClick: props.onClose }, "✕"),
        ]),
        h("div", { className: "hermes-lcm-drawer-body" }, props.children),
      ]),
    ]);
  }

  function DrawerError(props) {
    return h("div", { className: "hermes-lcm-derror" }, [
      h("div", { className: "hermes-lcm-derror-title" },
        "Couldn't load this " + (props.kind === "node" ? "node" : "session")),
      h("div", { className: "hermes-lcm-derror-msg" }, String(props.message || "Request failed")),
      props.onRetry ? h("button", {
        className: "hermes-lcm-btn hermes-lcm-derror-retry",
        onClick: props.onRetry,
      }, "↻ Retry") : null,
    ]);
  }

  function App() {
    const [q, setQ] = useState("");
    const [role, setRole] = useState("");
    const [source, setSource] = useState("");
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    const [searchData, setSearchData] = useState(null);
    const [searching, setSearching] = useState(false);

    const [timeline, setTimeline] = useState(null);
    const [compression, setCompression] = useState(null);

    // detail navigation stack: each entry {kind:'session'|'node', id, data, loading}
    const [stack, setStack] = useState([]);

    const debouncedQ = useMemo(function () { return q.trim(); }, [q]);

    // overview (cards + headline)
    useEffect(function () {
      let active = true;
      setLoading(true);
      setError("");
      SDK.fetchJSON(`${API}/overview?limit=25`).then(function (json) {
        if (active) setData(json);
      }).catch(function (err) {
        if (active) setError(String((err && err.message) || err));
      }).finally(function () {
        if (active) setLoading(false);
      });
      return function () { active = false; };
    }, []);

    // timeline + compression for charts
    useEffect(function () {
      let active = true;
      SDK.fetchJSON(`${API}/timeline?bucket=day&limit=400`).then(function (j) {
        if (active) setTimeline(j);
      }).catch(function () {});
      SDK.fetchJSON(`${API}/compression?by=session&limit=12`).then(function (j) {
        if (active) setCompression(j);
      }).catch(function () {});
      return function () { active = false; };
    }, []);

    // ranked search via FTS endpoint, with facets
    useEffect(function () {
      if (!debouncedQ) { setSearchData(null); return; }
      let active = true;
      setSearching(true);
      const params = new URLSearchParams();
      params.set("q", debouncedQ);
      params.set("limit", "30");
      if (role) params.set("role", role);
      if (source) params.set("source", source);
      SDK.fetchJSON(`${API}/search?${params.toString()}`).then(function (j) {
        if (active) setSearchData(j);
      }).catch(function (err) {
        if (active) setError(String((err && err.message) || err));
      }).finally(function () {
        if (active) setSearching(false);
      });
      return function () { active = false; };
    }, [debouncedQ, role, source]);

    const top = stack.length ? stack[stack.length - 1] : null;

    const fetchDetail = useCallback(function (kind, id) {
      const url = kind === "node" ? `${API}/node/${encodeURIComponent(id)}` : `${API}/session/${encodeURIComponent(id)}`;
      SDK.fetchJSON(url).then(function (j) {
        setStack(function (prev) {
          const next = prev.slice();
          for (let i = next.length - 1; i >= 0; i--) {
            if (next[i].kind === kind && String(next[i].id) === String(id)) {
              next[i] = { kind: kind, id: id, data: j, loading: false };
              return next;
            }
          }
          return next;
        });
      }).catch(function (err) {
        setStack(function (prev) {
          const next = prev.slice();
          for (let i = next.length - 1; i >= 0; i--) {
            if (next[i].kind === kind && String(next[i].id) === String(id)) {
              next[i] = { kind: kind, id: id, data: null, loading: false, error: String((err && err.message) || err) };
              return next;
            }
          }
          return next;
        });
      });
    }, []);

    const openDetail = useCallback(function (kind, id) {
      setStack(function (prev) { return prev.concat([{ kind: kind, id: id, data: null, loading: true }]); });
      fetchDetail(kind, id);
    }, [fetchDetail]);

    const openSession = useCallback(function (id) { openDetail("session", id); }, [openDetail]);
    const openNode = useCallback(function (id) { openDetail("node", id); }, [openDetail]);
    const goBack = useCallback(function () { setStack(function (prev) { return prev.slice(0, -1); }); }, []);
    const closeDrawer = useCallback(function () { setStack([]); }, []);

    const overview = (data && data.overview) || {};
    const comp = overview.compression || {};
    const sources = overview.source_counts || [];

    let drawerTitle = "";
    let drawerBody = null;
    if (top) {
      if (top.loading) {
        drawerTitle = top.kind === "node" ? `Node #${top.id}` : short(top.id, 40);
        drawerBody = h("div", { className: "hermes-lcm-empty" }, "Loading…");
      } else if (top.error) {
        drawerTitle = top.kind === "node" ? `Node #${top.id}` : short(top.id, 40);
        const _t = top;
        drawerBody = h(DrawerError, {
          kind: _t.kind,
          message: _t.error,
          onRetry: function () {
            setStack(function (prev) {
              const next = prev.slice();
              if (next.length) {
                next[next.length - 1] = { kind: _t.kind, id: _t.id, data: null, loading: true };
              }
              return next;
            });
            fetchDetail(_t.kind, _t.id);
          },
        });
      } else if (top.kind === "node") {
        drawerTitle = `Node #${top.id}`;
        drawerBody = h(NodeDetail, { data: top.data, onOpenNode: openNode, onOpenSession: openSession });
      } else {
        drawerTitle = `Session ${short(top.id, 40)}`;
        drawerBody = h(SessionDetail, { data: top.data, onOpenNode: openNode });
      }
    }

    const matches = (searchData && searchData.matches) || { messages: [], summary_nodes: [] };

    return h("div", { className: "hermes-lcm" }, [
      h("div", { className: "hermes-lcm-top" }, [
        h("input", {
          className: "hermes-lcm-search",
          value: q,
          placeholder: "Search messages and summaries (ranked FTS)",
          onChange: function (e) { setQ(e.target.value || ""); },
        }),
        h("select", {
          className: "hermes-lcm-select", value: role,
          onChange: function (e) { setRole(e.target.value); },
        }, [
          h("option", { key: "all", value: "" }, "All roles"),
          h("option", { key: "user", value: "user" }, "user"),
          h("option", { key: "assistant", value: "assistant" }, "assistant"),
          h("option", { key: "tool", value: "tool" }, "tool"),
          h("option", { key: "system", value: "system" }, "system"),
        ]),
        h("select", {
          className: "hermes-lcm-select", value: source,
          onChange: function (e) { setSource(e.target.value); },
        }, [h("option", { key: "all", value: "" }, "All sources")].concat(
          sources.map(function (s) {
            return h("option", { key: s.source, value: s.source }, short(s.source, 18));
          })
        )),
        h("div", { className: "hermes-lcm-status" },
          (loading || searching) ? "Loading…" : ((data && data.exists) ? "Database detected" : "Database missing")
        ),
      ]),
      h("div", { className: "hermes-lcm-path" }, data ? data.path : ""),
      error ? h("div", { className: "hermes-lcm-error" }, error) : null,
      data && data.error ? h("div", { className: "hermes-lcm-error" }, data.error) : null,

      // headline compression strip
      h("div", { className: "hermes-lcm-statrow" }, [
        h(Stat, { value: fmtInt(overview.messages_total), label: "messages" }),
        h(Stat, { value: fmtInt(overview.sessions_total), label: "sessions" }),
        h(Stat, { value: fmtInt(overview.summary_nodes_total), label: "summary nodes" }),
        h(Stat, { value: (comp.ratio ? comp.ratio + "×" : "—"), label: "compression" }),
        h(Stat, { value: `${fmtInt(comp.source_token_count)}→${fmtInt(comp.token_count)}`, label: "tokens kept" }),
      ]),

      h("div", { className: "hermes-lcm-grid" }, [
        h("div", { className: "hermes-lcm-card hermes-lcm-wide" }, [
          h("h3", null, "Message Timeline (per day · dots = summaries)"),
          h(TimelineChart, {
            buckets: (timeline && timeline.buckets) || [],
            nodeBuckets: (timeline && timeline.node_buckets) || [],
          }),
        ]),
        h("div", { className: "hermes-lcm-card hermes-lcm-wide" }, [
          h("h3", null, "Compression by Session (kept vs saved)"),
          h(CompressionBars, {
            groups: (compression && compression.groups) || [],
            onPick: function (g) { openSession(g.session_id != null ? g.session_id : g.key); },
          }),
        ]),
      ]),

      h("div", { className: "hermes-lcm-grid" }, [
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, "By Source"),
          h(BarList, { rows: sources, keyName: "source", onPick: function (v) { setSource(v === "(none)" ? "unknown" : v); } }),
        ]),
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, "By Role"),
          h(BarList, { rows: overview.role_counts || [], keyName: "role", onPick: function (v) { setRole(v); } }),
        ]),
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, "Summary Depth"),
          h(BarList, { rows: overview.depth_counts || [], keyName: "depth" }),
        ]),
      ]),

      h("div", { className: "hermes-lcm-grid" }, [
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, "Recent Sessions"),
          h("div", { className: "hermes-lcm-rows" },
            ((data && data.latest_sessions) || []).length
              ? ((data && data.latest_sessions) || []).map(function (s, idx) {
                  const tail = sessionTail(s.session_id);
                  return h("button", {
                    key: s.session_id + ":" + idx,
                    type: "button",
                    className: "hermes-lcm-row",
                    onClick: function () { openSession(s.session_id); },
                  }, [
                    h("div", { className: "hermes-lcm-row-main" }, [
                      h("span", { className: "hermes-lcm-row-title" }, sessionLabel(s.session_id)),
                      tail ? h("span", { className: "hermes-lcm-row-id" }, tail) : null,
                    ]),
                    h("div", { className: "hermes-lcm-row-meta" }, [
                      h("span", { className: "hermes-lcm-pill" }, fmtInt(s.message_count) + " msgs"),
                      s.last_timestamp ? h("span", { className: "hermes-lcm-dim" }, fmtTime(s.last_timestamp)) : null,
                    ]),
                  ]);
                })
              : h("div", { className: "hermes-lcm-empty" }, "No sessions")
          ),
        ]),
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, "Latest Summaries"),
          h("div", { className: "hermes-lcm-rows" },
            ((data && data.latest_summary_nodes) || []).length
              ? ((data && data.latest_summary_nodes) || []).map(function (n) {
                  const title = summaryTitle(n.summary);
                  const preview = stripMd(n.summary);
                  return h("button", {
                    key: n.node_id,
                    type: "button",
                    className: "hermes-lcm-row",
                    onClick: function () { openNode(n.node_id); },
                  }, [
                    h("div", { className: "hermes-lcm-row-meta" }, [
                      h("span", { className: "hermes-lcm-pill hermes-lcm-pill-accent" }, "D" + n.depth),
                      n.category ? h("span", { className: "hermes-lcm-pill" }, n.category) : null,
                      h("span", { className: "hermes-lcm-dim" }, sessionLabel(n.session_id)),
                      n.token_count != null ? h("span", { className: "hermes-lcm-dim" }, fmtInt(n.token_count) + " tok") : null,
                    ]),
                    h("div", { className: "hermes-lcm-row-title" }, short(title, 80)),
                    h("div", { className: "hermes-lcm-row-sub" }, short(preview, 150)),
                  ]);
                })
              : h("div", { className: "hermes-lcm-empty" }, "No summaries")
          ),
        ]),
      ]),

      debouncedQ ? h("div", { className: "hermes-lcm-grid" }, [
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, `Matching Messages (${(matches.messages || []).length}${searchData && searchData.engine ? " · " + searchData.engine : ""})`),
          h("div", { className: "hermes-lcm-results" }, (matches.messages || []).map(function (m) {
            return h("div", {
              key: m.store_id,
              className: "hermes-lcm-result hermes-lcm-clk",
              onClick: function () { openSession(m.session_id); },
            }, [
              h("div", { className: "hermes-lcm-msg-meta" }, [
                h("span", { className: "hermes-lcm-tag" }, m.role),
                m.source ? h("span", { className: "hermes-lcm-tag hermes-lcm-tag-src" }, m.source) : null,
                h("span", { className: "hermes-lcm-dim" }, short(m.session_id, 24)),
                h("span", { className: "hermes-lcm-dim" }, fmtTime(m.timestamp)),
              ]),
              h("div", { className: "hermes-lcm-msg-body" }, renderSnippet(m.snippet || short(m.content, 200))),
            ]);
          })),
        ]),
        h("div", { className: "hermes-lcm-card" }, [
          h("h3", null, `Matching Summaries (${(matches.summary_nodes || []).length})`),
          h("div", { className: "hermes-lcm-results" }, (matches.summary_nodes || []).map(function (n) {
            return h("div", {
              key: n.node_id,
              className: "hermes-lcm-result hermes-lcm-clk",
              onClick: function () { openNode(n.node_id); },
            }, [
              h("div", { className: "hermes-lcm-msg-meta" }, [
                h("span", { className: "hermes-lcm-tag" }, `D${n.depth}`),
                n.category ? h("span", { className: "hermes-lcm-tag" }, n.category) : null,
                h("span", { className: "hermes-lcm-dim" }, short(n.session_id, 24)),
              ]),
              h("div", { className: "hermes-lcm-msg-body" }, renderSnippet(n.snippet || short(n.summary, 200))),
            ]);
          })),
        ]),
      ]) : null,

      h(Drawer, {
        open: !!top,
        title: drawerTitle,
        canBack: stack.length > 1,
        onBack: goBack,
        onClose: closeDrawer,
      }, drawerBody),
    ]);
  }

  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("hermes-lcm", App);
  }
})();
