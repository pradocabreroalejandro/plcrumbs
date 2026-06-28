#!/usr/bin/env python3
"""
br_graph_render.py  —  Render an interactive D3.js graph from br_extractor JSON.

Usage:
  python br_graph_render.py graph_data.json > br_graph.html
  python br_graph_render.py graph_data.json -o br_graph.html

Reads the deterministic JSON produced by `br_extractor.py --json` and embeds
it into a self-contained HTML file with D3.js force-directed graph.

No external dependencies beyond Python 3.8+. The HTML is fully self-contained
(one file, including inline CSS/JS, CDN-loaded D3). No server needed — open
directly in a browser.

The renderer is independent from the extractor. The only contract is the JSON
shape produced by `build_graph_data()` in br_extractor.py.
"""
import argparse
import json
import sys
from pathlib import Path


def build_html(data):
    """Render an interactive D3.js graph HTML with the given data embedded."""
    package = _guess_package_name(data)
    orchestrators, hubs, entries = _compute_roles(data)
    call_count = len(data.get("calls", []))
    lat_count = len(data.get("lateral", []))
    raw_json = json.dumps(data, separators=(",", ":"))

    html = _HTML_TEMPLATE
    subs = {
        "PACKAGE": package,
        "CALL_COUNT": str(call_count),
        "LAT_COUNT": str(lat_count),
        "NODE_COUNT": str(len(data.get("nodes", []))),
        "RAW_JSON": raw_json,
        "ORCHESTRATORS_JSON": json.dumps(orchestrators),
        "HUBS_JSON": json.dumps(hubs),
        "ENTRIES_JSON": json.dumps(entries),
    }
    for key, val in subs.items():
        html = html.replace("{{" + key + "}}", val)
    return html


def _guess_package_name(data):
    ids = [n["id"] for n in data.get("nodes", [])]
    if not ids:
        return "BR Graph"
    prefix = ids[0]
    for nid in ids[1:]:
        while not nid.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                break
    prefix = prefix.rstrip("_")
    return prefix.upper() if prefix else "BR Graph"


def _compute_roles(data):
    out_calls = {n["id"]: 0 for n in data["nodes"]}
    for c in data.get("calls", []):
        if c["s"] in out_calls:
            out_calls[c["s"]] += 1

    writes = {}
    for lat in data.get("lateral", []):
        writes.setdefault(lat["s"], set()).add(lat["v"])
    write_counts = {n["id"]: len(writes.get(n["id"], set())) for n in data["nodes"]}

    in_calls = {n["id"]: 0 for n in data["nodes"]}
    for c in data.get("calls", []):
        if c["t"] in in_calls:
            in_calls[c["t"]] += 1

    orchestrators = []
    hubs = []
    entries = []

    for n in data["nodes"]:
        nid = n["id"]
        if out_calls.get(nid, 0) >= 2 and write_counts.get(nid, 0) > 0:
            orchestrators.append(nid)
        elif write_counts.get(nid, 0) >= 2 and out_calls.get(nid, 0) <= 1:
            hubs.append(nid)
        if in_calls.get(nid, 0) == 0:
            entries.append(nid)

    return orchestrators, hubs, entries


# ---- HTML template -----------------------------------------------------------
# Uses {{PLACEHOLDER}} markers (NOT %-formatting) to avoid clashes with CSS %.

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>plcrumbs · {{PACKAGE}} — BR Graph</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f1117; color: #c9d1d9; font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 13px; overflow: hidden; }

  #canvas { width: 100vw; height: 100vh; }

  .link-call { stroke: #444c56; stroke-width: 1.5; fill: none; marker-end: url(#arrow-call); }
  .link-call.hi  { stroke: #58a6ff; stroke-width: 2; }
  .link-lateral { stroke: #4d3a1a; stroke-width: 1; fill: none; stroke-dasharray: 4,3; opacity: 0.7; marker-end: url(#arrow-lat); }
  .link-lateral.hi { stroke: #e3b341; stroke-width: 1.5; opacity: 1; }

  circle { cursor: pointer; stroke-width: 1.5; transition: stroke 0.15s; }
  circle:hover { stroke: #fff !important; }

  .label { pointer-events: none; fill: #e6edf3; font-size: 10px; text-anchor: middle; }
  .label-sm { font-size: 8.5px; fill: #8b949e; }

  #tip {
    position: fixed; pointer-events: none; display: none;
    background: #161b22; border: 1px solid #30363d; border-radius: 6px;
    padding: 10px 14px; font-size: 12px; line-height: 1.7; min-width: 240px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.5);
  }
  #tip strong { color: #58a6ff; }
  #tip .badge { display: inline-block; padding: 0 6px; border-radius: 4px; font-size: 10px; margin-right: 3px; }
  .b-leaf { background: #1f4b6b; color: #79c0ff; }
  .b-expr { background: #2d3b1f; color: #7ee787; }
  .b-state { background: #3b2d4a; color: #d2a8ff; }
  .b-flow { background: #4b3320; color: #ffa657; }
  .b-assert { background: #4a2c1a; color: #ffb347; }
  .b-exit { background: #3b1f4a; color: #e08aff; }
  .b-exception { background: #4a1f1f; color: #ff6b6b; }

  #panel {
    position: fixed; top: 16px; right: 16px; width: 270px;
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 14px; font-size: 12px;
  }
  #panel h2 { font-size: 13px; color: #e6edf3; margin-bottom: 10px; }
  #panel h3 { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: .05em; margin: 10px 0 6px; }
  .toggle-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; cursor: pointer; }
  .toggle-row input { cursor: pointer; }
  .dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  #lateral-info { margin-top: 6px; padding: 6px 8px; background: #0d1117; border-radius: 4px; color: #8b949e; font-size: 11px; min-height: 24px; }
  #stats { margin-top: 10px; color: #8b949e; font-size: 11px; line-height: 1.8; }
  .stat-row { display: flex; justify-content: space-between; }
  .stat-val { color: #e6edf3; }

  #legend {
    position: fixed; bottom: 16px; left: 16px;
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 12px 14px; font-size: 11px; line-height: 1.9;
  }
  #legend h3 { font-size: 10px; color: #8b949e; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 5px; }
  .leg-row { display: flex; align-items: center; gap: 8px; }
  .leg-dot { width: 12px; height: 12px; border-radius: 50%; }

  #hint { position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%); color: #484f58; font-size: 11px; pointer-events: none; }
</style>
</head>
<body>

<svg id="canvas">
  <defs>
    <marker id="arrow-call" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L0,6 L8,3 z" fill="#444c56"/>
    </marker>
    <marker id="arrow-lat" markerWidth="6" markerHeight="6" refX="5" refY="2.5" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L0,5 L6,2.5 z" fill="#4d3a1a"/>
    </marker>
    <marker id="arrow-call-hi" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L0,6 L8,3 z" fill="#58a6ff"/>
    </marker>
    <marker id="arrow-lat-hi" markerWidth="6" markerHeight="6" refX="5" refY="2.5" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L0,5 L6,2.5 z" fill="#e3b341"/>
    </marker>
  </defs>
  <g id="g-main"></g>
</svg>

<div id="tip"></div>

<div id="panel">
  <h2>&#x1f9e9 {{PACKAGE}}</h2>

  <h3>Edges</h3>
  <label class="toggle-row">
    <input type="checkbox" id="chk-calls" checked>
    <span class="dot" style="background:#444c56; border: 1px solid #58a6ff"></span>
    Call graph ({{CALL_COUNT}})
  </label>
  <label class="toggle-row">
    <input type="checkbox" id="chk-lateral">
    <span class="dot" style="background:#4d3a1a; border: 1px dashed #e3b341"></span>
    Lateral / pkg-state ({{LAT_COUNT}})
  </label>
  <div id="lateral-info">Click a node to see its lateral edges</div>

  <h3>Stats</h3>
  <div id="stats">
    <div class="stat-row"><span>Procedures</span><span class="stat-val" id="s-procs">{{NODE_COUNT}}</span></div>
    <div class="stat-row"><span>Unique names</span><span class="stat-val" id="s-names">&ndash;</span></div>
    <div class="stat-row"><span>LEAF rules</span><span class="stat-val" id="s-rules">&ndash;</span></div>
    <div class="stat-row"><span>ASSERT</span><span class="stat-val" id="s-asserts">&ndash;</span></div>
    <div class="stat-row"><span>EXPR blocks</span><span class="stat-val" id="s-exprs">&ndash;</span></div>
    <div class="stat-row"><span>STATE markers</span><span class="stat-val" id="s-states">&ndash;</span></div>
    <div class="stat-row"><span>EXIT (early return)</span><span class="stat-val" id="s-exits">&ndash;</span></div>
    <div class="stat-row"><span>pkg vars</span><span class="stat-val" id="s-vars">&ndash;</span></div>
    <div class="stat-row"><span>Config refs</span><span class="stat-val" id="s-configs">&ndash;</span></div>
    <div class="stat-row"><span>Exception handlers</span><span class="stat-val" id="s-exceptions">&ndash;</span></div>
  </div>
</div>

<div id="legend">
  <h3>Node roles</h3>
  <div class="leg-row"><div class="leg-dot" style="background:#f0883e"></div> Orchestrator</div>
  <div class="leg-row"><div class="leg-dot" style="background:#9e6fe5"></div> Hub (pkg-state init)</div>
  <div class="leg-row"><div class="leg-dot" style="background:#56d364"></div> Entry point</div>
  <div class="leg-row"><div class="leg-dot" style="background:#388bfd"></div> Standard</div>
  <div class="leg-row"><div class="leg-dot" style="background:#3d444d; border: 1px solid #484f58"></div> Empty wrapper</div>
  <div class="leg-row"><div class="leg-dot" style="background:#f14c4c"></div> Has EXCEPTION handler</div>
  <br>
  <div class="leg-row" style="color:#8b949e">Size ~ total annotations + expr_lines</div>
</div>

<div id="hint">scroll to zoom &middot; drag to pan &middot; click node to highlight</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script>
const RAW = {{RAW_JSON}};

const nameCount = {};
const nodes = RAW.nodes.map(n => {
  nameCount[n.id] = (nameCount[n.id] || 0) + 1;
  return { ...n, _uid: n.id + (nameCount[n.id] > 1 ? '_v' + nameCount[n.id] : '') };
});
const uidByName = {};
const seenCount = {};
RAW.nodes.forEach(n => {
  seenCount[n.id] = (seenCount[n.id] || 0) + 1;
  const uid = seenCount[n.id] > 1 ? n.id + '_v' + seenCount[n.id] : n.id;
  if (!uidByName[n.id]) uidByName[n.id] = [];
  uidByName[n.id].push(uid);
});

{
  const cnt2 = {};
  nodes.forEach(n => {
    cnt2[n.id] = (cnt2[n.id] || 0) + 1;
    n._uid = cnt2[n.id] > 1 ? n.id + '_v' + cnt2[n.id] : n.id;
    n.display = n.id;
    if (cnt2[n.id] > 1) n.display = n.id + ' (v' + cnt2[n.id] + ')';
  });
}

const nodeMap = new Map(nodes.map(n => [n._uid, n]));
const firstUid = name => uidByName[name]?.[0] ?? name;

const callEdges = RAW.calls.map(e => ({
  source: firstUid(e.s), target: firstUid(e.t), type: 'call'
}));

const latMap = new Map();
RAW.lateral.forEach(e => {
  const k = firstUid(e.s) + '\u2192' + firstUid(e.t);
  if (!latMap.has(k)) latMap.set(k, { source: firstUid(e.s), target: firstUid(e.t), vars: [], type: 'lateral' });
  latMap.get(k).vars.push(e.v);
});
const latEdges = Array.from(latMap.values());

const ORCHESTRATORS = new Set({{ORCHESTRATORS_JSON}});
const HUBS = new Set({{HUBS_JSON}});
const ENTRIES = new Set({{ENTRIES_JSON}});

function nodeColor(n) {
  const base = n.id;
  if (ORCHESTRATORS.has(base)) return '#f0883e';
  if (HUBS.has(base)) return '#9e6fe5';
  if (ENTRIES.has(base)) return '#56d364';
  const total = n.rules + n.asserts + n.exprs + n.states + n.flows + n.exits;
  if (total === 0) return '#3d444d';
  if (n.has_exception) return '#f14c4c';
  return '#388bfd';
}

function nodeR(n) {
  const totalAnnots = n.rules + n.asserts + n.exprs + n.states + n.flows + n.exits;
  const weight = totalAnnots + (n.expr_lines || 0) * 0.3;
  return Math.max(8, Math.sqrt(weight) * 3.5);
}

document.getElementById('s-names').textContent = Object.keys(uidByName).length;
document.getElementById('s-rules').textContent = nodes.reduce((a,n)=>a+n.rules,0);
document.getElementById('s-asserts').textContent = nodes.reduce((a,n)=>a+(n.asserts||0),0);
document.getElementById('s-exprs').textContent = nodes.reduce((a,n)=>a+n.exprs,0);
document.getElementById('s-states').textContent = nodes.reduce((a,n)=>a+n.states,0);
document.getElementById('s-exits').textContent = nodes.reduce((a,n)=>a+(n.exits||0),0);
document.getElementById('s-vars').textContent = new Set(RAW.lateral.map(e=>e.v)).size;
document.getElementById('s-configs').textContent = nodes.reduce((a,n)=>a+(n.configs||0),0);
document.getElementById('s-exceptions').textContent = nodes.reduce((a,n)=>a+(n.has_exception?1:0),0);

const svg = d3.select('#canvas');
const g = d3.select('#g-main');
const W = window.innerWidth, H = window.innerHeight;

const zoom = d3.zoom().scaleExtent([0.2, 4]).on('zoom', e => g.attr('transform', e.transform));
svg.call(zoom);
svg.call(zoom.transform, d3.zoomIdentity.translate(W/2, H/2));

const sim = d3.forceSimulation(nodes)
  .force('link', d3.forceLink(callEdges).id(d => d._uid).distance(d => {
    const r1 = nodeR(d.source), r2 = nodeR(d.target);
    return 80 + r1 + r2;
  }).strength(0.4))
  .force('charge', d3.forceManyBody().strength(-350))
  .force('center', d3.forceCenter(0, 0))
  .force('collide', d3.forceCollide(d => nodeR(d) + 18))
  .alphaDecay(0.025);

const gLat = g.append('g').attr('class', 'lat-layer');
const gCall = g.append('g').attr('class', 'call-layer');

const latLink = gLat.selectAll('path')
  .data(latEdges)
  .join('path')
  .attr('class', 'link-lateral')
  .style('display', 'none');

const callLink = gCall.selectAll('path')
  .data(callEdges)
  .join('path')
  .attr('class', 'link-call');

const gNode = g.append('g');

const node = gNode.selectAll('g')
  .data(nodes)
  .join('g')
  .attr('class', 'node-g')
  .call(d3.drag()
    .on('start', (e,d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; })
    .on('drag', (e,d) => { d.fx=e.x; d.fy=e.y; })
    .on('end', (e,d) => { if (!e.active) sim.alphaTarget(0); d.fx=null; d.fy=null; })
  );

node.append('circle')
  .attr('r', d => nodeR(d))
  .attr('fill', d => nodeColor(d))
  .attr('fill-opacity', 0.9)
  .attr('stroke', d => d3.color(nodeColor(d)).darker(0.8));

node.append('text')
  .attr('class', 'label')
  .attr('dy', d => nodeR(d) + 12)
  .text(d => d.display.replace(/_/g, '_\u200b'))
  .each(function(d) {
    const txt = d.display;
    if (txt.length > 26) {
      const parts = txt.split('_');
      const abbr = parts.map((p,i) => i === parts.length-1 ? p : p[0]).join('_');
      d3.select(this).text(abbr);
      d3.select(this).append('title').text(txt);
    }
  });

const tip = document.getElementById('tip');
let selectedNode = null;

node.on('mousemove', function(event, d) {
  tip.style.display = 'block';
  tip.style.left = (event.clientX + 14) + 'px';
  tip.style.top  = (event.clientY - 10) + 'px';
  const extras = [];
  if (d.asserts && d.asserts > 0) extras.push('<span class="badge b-assert">ASSERT ' + d.asserts + '</span>');
  if (d.exits && d.exits > 0) extras.push('<span class="badge b-exit">EXIT ' + d.exits + '</span>');
  if (d.has_exception) extras.push('<span class="badge b-exception">\u26a0 EXCEPTION</span>');
  tip.innerHTML = [
    '<strong>' + d.display + '</strong><br>',
    '<span style="color:#8b949e">L' + d.line + '</span><br><br>',
    '<span class="badge b-leaf">LEAF ' + d.rules + '</span>',
    '<span class="badge b-expr">EXPR ' + d.exprs + '</span>',
    '<span class="badge b-state">STATE ' + d.states + '</span>',
    '<span class="badge b-flow">FLOW ' + d.flows + '</span>',
    extras.join(' '),
  ].join('');
}).on('mouseleave', function() {
  tip.style.display = 'none';
});

const latInfo = document.getElementById('lateral-info');

node.on('click', function(event, d) {
  event.stopPropagation();
  if (selectedNode === d._uid) {
    selectedNode = null;
    resetHighlight();
    latInfo.textContent = 'Click a node to see its lateral edges';
    return;
  }
  selectedNode = d._uid;

  const callNeighbors = new Set([d._uid]);
  callEdges.forEach(e => {
    const s = typeof e.source === 'object' ? e.source._uid : e.source;
    const t = typeof e.target === 'object' ? e.target._uid : e.target;
    if (s === d._uid) callNeighbors.add(t);
    if (t === d._uid) callNeighbors.add(s);
  });

  const myLat = latEdges.filter(e => {
    const s = typeof e.source === 'object' ? e.source._uid : e.source;
    const t = typeof e.target === 'object' ? e.target._uid : e.target;
    return s === d._uid || t === d._uid;
  });
  const latNeighbors = new Set([d._uid]);
  myLat.forEach(e => {
    const s = typeof e.source === 'object' ? e.source._uid : e.source;
    const t = typeof e.target === 'object' ? e.target._uid : e.target;
    latNeighbors.add(s); latNeighbors.add(t);
  });

  node.select('circle')
    .attr('fill-opacity', nd => callNeighbors.has(nd._uid) || latNeighbors.has(nd._uid) ? 0.95 : 0.2)
    .attr('stroke', nd => nd._uid === d._uid ? '#fff' : (callNeighbors.has(nd._uid) ? '#58a6ff' : (latNeighbors.has(nd._uid) ? '#e3b341' : d3.color(nodeColor(nd)).darker(0.8))));

  callLink
    .classed('hi', e => {
      const s = typeof e.source === 'object' ? e.source._uid : e.source;
      const t = typeof e.target === 'object' ? e.target._uid : e.target;
      return s === d._uid || t === d._uid;
    })
    .attr('stroke-opacity', e => {
      const s = typeof e.source === 'object' ? e.source._uid : e.source;
      const t = typeof e.target === 'object' ? e.target._uid : e.target;
      return (s === d._uid || t === d._uid) ? 1 : 0.12;
    });

  latLink
    .style('display', e => {
      const s = typeof e.source === 'object' ? e.source._uid : e.source;
      const t = typeof e.target === 'object' ? e.target._uid : e.target;
      return (s === d._uid || t === d._uid) ? null : 'none';
    })
    .classed('hi', true);

  const varSummary = {};
  myLat.forEach(e => {
    e.vars.forEach(v => { varSummary[v] = (varSummary[v]||0)+1; });
  });
  const varList = Object.entries(varSummary).sort((a,b)=>b[1]-a[1]).slice(0,8)
    .map(([v]) => '<code style="color:#e3b341">' + v + '</code>').join(' ');
  latInfo.innerHTML = '<b>' + myLat.length + '</b> lateral edges via:<br>' + varList;
});

svg.on('click', () => {
  if (selectedNode) {
    selectedNode = null;
    resetHighlight();
    latInfo.textContent = 'Click a node to see its lateral edges';
  }
});

function resetHighlight() {
  node.select('circle')
    .attr('fill-opacity', 0.9)
    .attr('stroke', d => d3.color(nodeColor(d)).darker(0.8));
  callLink.classed('hi', false).attr('stroke-opacity', 1);
  const showLat = document.getElementById('chk-lateral').checked;
  latLink.style('display', showLat ? null : 'none').classed('hi', false);
}

document.getElementById('chk-calls').addEventListener('change', function() {
  callLink.style('display', this.checked ? null : 'none');
});
document.getElementById('chk-lateral').addEventListener('change', function() {
  if (!selectedNode) {
    latLink.style('display', this.checked ? null : 'none');
  }
});

function linkArc(d) {
  const sx = typeof d.source === 'object' ? d.source.x : 0;
  const sy = typeof d.source === 'object' ? d.source.y : 0;
  const tx = typeof d.target === 'object' ? d.target.x : 0;
  const ty = typeof d.target === 'object' ? d.target.y : 0;
  const dx = tx - sx, dy = ty - sy;
  const dist = Math.sqrt(dx*dx+dy*dy) || 1;

  const tr = typeof d.target === 'object' ? nodeR(d.target) + 8 : 15;
  const ex = tx - (dx/dist)*tr;
  const ey = ty - (dy/dist)*tr;

  const curve = d.type === 'lateral' ? 0.35 : 0.15;
  const mx = (sx+tx)/2 - dy*curve;
  const my = (sy+ty)/2 + dx*curve;
  return 'M' + sx + ',' + sy + ' Q' + mx + ',' + my + ' ' + ex + ',' + ey;
}

sim.on('tick', () => {
  callLink.attr('d', linkArc);
  latLink.attr('d', linkArc);
  node.attr('transform', d => 'translate(' + d.x + ',' + d.y + ')');
});

window.addEventListener('resize', () => {
  sim.force('center', d3.forceCenter(0,0)).alpha(0.1).restart();
});
</script>
</body>
</html>"""


# ---- entry point -------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Render an interactive D3.js graph from br_extractor JSON.",
    )
    parser.add_argument("input", help="Path to graph_data.json (from br_extractor.py --json)")
    parser.add_argument("-o", "--output", help="Output HTML file (default: stdout)")

    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        sys.exit(f"File not found: {src}")

    with open(src, encoding="utf-8") as f:
        data = json.load(f)

    html = build_html(data)

    if args.output:
        Path(args.output).write_text(html, encoding="utf-8")
        print(f"Generated: {args.output}")
    else:
        sys.stdout.write(html)


if __name__ == "__main__":
    main()