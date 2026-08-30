// 注入页面执行：找出**真的折了行**的文本节点，并顺带量几个几何判据。
// 判据不是「类名写没写」（那是 jsdom 用例的活），而是 Range.getClientRects().length > 1。
module.exports = function auditSource() {
  return `(() => {
  const root = document.querySelector("main") || document.body;

  function cssPath(el) {
    const parts = [];
    for (let n = el; n && n !== document.body && parts.length < 6; n = n.parentElement) {
      let seg = n.tagName.toLowerCase();
      const tid = n.getAttribute && n.getAttribute("data-testid");
      if (tid) { seg += '[data-testid="' + tid + '"]'; parts.unshift(seg); break; }
      const nw = n.getAttribute && n.getAttribute("data-nowrap");
      if (nw) seg += '[data-nowrap="' + nw + '"]';
      const role = n.getAttribute && n.getAttribute("role");
      if (role) seg += '[role="' + role + '"]';
      parts.unshift(seg);
    }
    return parts.join(" > ");
  }

  function hasClassUp(el, cls) {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      if (n.classList && n.classList.contains(cls)) return true;
    }
    return false;
  }

  // ── 1) 真实折行扫描 ────────────────────────────────────────────────
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const wrapped = [];
  let node;
  while ((node = walker.nextNode())) {
    const text = (node.nodeValue || "").trim();
    if (text.length < 2) continue;
    const el = node.parentElement;
    if (!el) continue;
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") continue;
    const r = document.createRange();
    r.selectNodeContents(node);
    const rects = r.getClientRects();
    if (rects.length <= 1) continue;
    // 同一行里被 <br> 之外的行内元素切开不算；只认 y 不同的行盒。
    const ys = new Set();
    for (const rect of rects) { if (rect.width > 0 && rect.height > 0) ys.add(Math.round(rect.top)); }
    if (ys.size <= 1) continue;
    wrapped.push({
      text: text.length > 60 ? text.slice(0, 60) + "…" : text,
      lines: ys.size,
      whiteSpace: cs.whiteSpace,
      declaredNormal: hasClassUp(el, "whitespace-normal"),
      insideNowrapAnchor: !!el.closest("[data-nowrap]"),
      path: cssPath(el),
    });
  }

  // ── 2) 锚点自证：每个 data-nowrap 的 computed white-space 必须真的是 nowrap ──
  const anchors = Array.from(document.querySelectorAll("[data-nowrap]")).map((el) => ({
    value: el.getAttribute("data-nowrap"),
    whiteSpace: getComputedStyle(el).whiteSpace,
    overflowing: el.scrollWidth > el.clientWidth + 1,
    width: Math.round(el.getBoundingClientRect().width),
    path: cssPath(el),
  }));

  // ── 3) 轴 E 几何 ──────────────────────────────────────────────────
  function box(sel) {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height), sw: el.scrollWidth, cw: el.clientWidth };
  }
  const byClass = (frag) => Array.from(document.querySelectorAll("div")).find(
    (el) => typeof el.className === "string" && el.className.includes(frag),
  ) || null;
  const sidebarEl = Array.from(document.querySelectorAll("div[style]")).find(
    (el) => el.style.width && typeof el.className === "string" && el.className.includes("min-w-[200px]"),
  ) || null;
  const centerEl = byClass("min-w-[240px]");
  const depthEl = Array.from(document.querySelectorAll("div[style]")).find(
    (el) => el.style.flexBasis && typeof el.className === "string" && el.className.includes("max-w-[420px]"),
  ) || null;
  const rect = (el) => (el ? Math.round(el.getBoundingClientRect().width) : null);
  const geom = {
    viewport: window.innerWidth,
    docScrollWidth: document.documentElement.scrollWidth,
    horizontalOverflow: document.documentElement.scrollWidth - window.innerWidth,
    sidebar: rect(sidebarEl),
    sidebarStyleWidth: sidebarEl ? sidebarEl.style.width : null,
    sidebarScrollX: sidebarEl ? sidebarEl.scrollWidth - sidebarEl.clientWidth : null,
    center: rect(centerEl),
    depthWidth: rect(depthEl),
    depthFlexShrink: depthEl ? getComputedStyle(depthEl).flexShrink : null,
    sidebarFlexShrink: sidebarEl ? getComputedStyle(sidebarEl).flexShrink : null,
    depth: box('[data-testid="market-depth-panel"]'),
    toolbar: box('[data-testid="chart-toolbar"]'),
    headerBar: box('[data-testid="chart-header-bar"]'),
    autoHeaderRow: box('[data-testid="auto-trade-header-row"]'),
    autoSummaryRow: box('[data-testid="auto-trade-summary-row"]'),
  };

  // ── 4) 表格：Type C 之后应当横滚而不是折表头 ─────────────────────
  const tables = Array.from(document.querySelectorAll("table")).map((t) => {
    let host = t.parentElement;
    while (host && host !== document.body) {
      const ov = getComputedStyle(host).overflowX;
      if (ov === "auto" || ov === "scroll") break;
      host = host.parentElement;
    }
    return {
      cls: t.className,
      tableWidth: Math.round(t.getBoundingClientRect().width),
      scrollHost: host && host !== document.body ? cssPath(host) : null,
      hostScrollable: host && host !== document.body ? host.scrollWidth > host.clientWidth + 1 : false,
      clipped: !host || host === document.body,
    };
  });

  return { wrapped, anchors, geom, tables };
})()`;
};
