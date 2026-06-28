#!/usr/bin/env python3
"""
br_extractor.py  --  Business Rule extractor for plcrumbs-annotated PL/SQL.

Usage: python br_extractor.py <src_file> [proc_name ...]

Vocabulary (five families):
  [CONFIG:scope:key]        -- config tree input
  [BR:LEAF(entity:attr)]    -- decision point (IF/CASE)
  [BR:EXPR:BEGIN/END]       -- produces a value (calc, cursor, aggregation)
  [BR:EXPR]                 -- same, inline single-line
  [BR:STATE]                -- flow state (flag, sentinel, counter)
  [BR:FLOW]                 -- control transfer (GOTO, label, routing)

Debt marker (not graphed):
  [BR:TODO]                 -- unclassified; counted and listed as warning

The code is the truth -- crumbs only mark WHERE.
"""
import re
import sys
import json
from pathlib import Path
from dataclasses import dataclass, field


# ---- data model --------------------------------------------------------------

@dataclass
class BrExpr:
    sql: list       # code lines (multi-line block or single-element inline)
    line_no: int

@dataclass
class BrState:
    code: str       # single line carrying flow state
    line_no: int

@dataclass
class BrFlow:
    target: str     # GOTO label, label name, or RETURN/EXIT
    condition: str  # IF/ELSIF condition that triggers this transfer
    line_no: int

@dataclass
class BrLeaf:
    entity: str
    attr: str
    condition: str  # extracted from IF guard in source
    line_no: int

@dataclass
class ConfigRef:
    scope: str
    key: str
    param: str
    line_no: int

@dataclass
class PkgStateRef:
    var: str
    direction: str  # 'read' or 'write'
    line_no: int

@dataclass
class ProcedureNode:
    name: str
    line_no: int
    params_in: list  = field(default_factory=list)
    params_out: list = field(default_factory=list)
    configs: list    = field(default_factory=list)
    leaves: list     = field(default_factory=list)
    exprs: list      = field(default_factory=list)   # BrExpr
    states: list     = field(default_factory=list)   # BrState
    flows: list      = field(default_factory=list)   # BrFlow
    calls: list      = field(default_factory=list)
    pkg_state: list  = field(default_factory=list)
    todos: list      = field(default_factory=list)   # (code, line_no)


# ---- regexes -----------------------------------------------------------------

RE_PROC      = re.compile(r'^\s*PROCEDURE\s+(\w+)', re.IGNORECASE)
RE_FUNC      = re.compile(r'^\s*FUNCTION\s+(\w+)',  re.IGNORECASE)
RE_PARAM_IN  = re.compile(r'\b(\w+)\s+IN\b(?!\s+OUT)', re.IGNORECASE)
RE_PARAM_OUT = re.compile(r'\b(\w+)\s+(?:IN\s+OUT|OUT)\b', re.IGNORECASE)
RE_CONFIG    = re.compile(r'\[CONFIG:(\w+):(\w+)\]')
RE_BR_LEAF   = re.compile(r'\[BR:LEAF\((\w+):(\w+)\)\]')
RE_BR_EXPR_B = re.compile(r'\[BR:EXPR:BEGIN\]')
RE_BR_EXPR_E = re.compile(r'\[BR:EXPR:END\]')
RE_BR_EXPR   = re.compile(r'\[BR:EXPR\](?!:)')    # inline, not BEGIN/END
RE_BR_STATE  = re.compile(r'\[BR:STATE\]')
RE_BR_FLOW   = re.compile(r'\[BR:FLOW\]')
RE_BR_TODO   = re.compile(r'\[BR:TODO\]')
RE_PKG_WRITE = re.compile(r'\b(v_\w+)\s*:=')
RE_PKG_READ  = re.compile(r'\b(v_\w+)\b')

KW_SKIP = {
    'if', 'loop', 'while', 'for', 'case', 'when', 'exit', 'begin', 'end',
    'exception', 'raise', 'return', 'select', 'insert', 'update', 'delete',
    'with', 'fetch', 'open', 'close', 'execute', 'commit', 'rollback', 'null',
    'nvl', 'decode', 'trunc', 'to_date', 'to_char', 'sum', 'count', 'max',
    'min', 'substr', 'instr', 'length', 'upper', 'lower', 'trim',
}
KW_PARAM = {
    'IN', 'OUT', 'NUMBER', 'VARCHAR2', 'DATE', 'BOOLEAN', 'CHAR',
    'INTEGER', 'PLS_INTEGER', 'BINARY_INTEGER', 'CLOB', 'BLOB',
    'TYPE', 'DEFAULT', 'NULL', 'RETURN', 'PROCEDURE', 'FUNCTION',
}


# ---- helpers -----------------------------------------------------------------

def _sig_block(lines, start):
    block = ""
    for ln in lines[start: start + 30]:
        block += ln
        if re.search(r'\b(IS|AS)\b', ln, re.IGNORECASE):
            break
    ins  = [p for p in RE_PARAM_IN.findall(block)  if p.upper() not in KW_PARAM]
    outs = [p for p in RE_PARAM_OUT.findall(block) if p.upper() not in KW_PARAM]
    return ins, outs


def _extract_condition(line):
    m = re.match(r'^\s*(?:IF|ELSIF)\s+(.+)', line, re.IGNORECASE)
    if not m:
        return line.strip()
    cond = m.group(1).strip()
    cond = re.sub(r'\s*--.*$', '', cond).strip()
    cond = re.sub(r'\s+THEN\s*$', '', cond).strip()
    return cond


def _is_end_block(line):
    """True only for bare END / END procname -- NOT END IF, END LOOP, END CASE."""
    if not re.match(r'^\s*END\b', line, re.IGNORECASE):
        return False
    if re.match(r'^\s*END\s+(IF|LOOP|CASE|FOR|WHILE)\b', line, re.IGNORECASE):
        return False
    return True


# ---- parser ------------------------------------------------------------------

def parse(src_lines):
    nodes       = []
    current     = None
    in_sig      = False
    in_body     = False
    depth       = 0
    expr_active = False
    expr_start  = 0
    expr_lines  = []

    def _save_expr():
        nonlocal expr_active, expr_lines
        if current and expr_active:
            sql = [l.rstrip() for l in expr_lines
                   if l.strip() and not l.strip().startswith('--')]
            current.exprs.append(BrExpr(sql=sql, line_no=expr_start))
        expr_active = False
        expr_lines  = []

    for i, raw in enumerate(src_lines):
        line    = raw.rstrip('\n')
        line_no = i + 1

        # -- procedure / function start
        m_proc = RE_PROC.match(line) or RE_FUNC.match(line)
        if m_proc:
            ins, outs = _sig_block(src_lines, i)
            current = ProcedureNode(
                name=m_proc.group(1).lower(),
                line_no=line_no,
                params_in=ins,
                params_out=outs,
            )
            nodes.append(current)
            in_sig      = True
            in_body     = False
            depth       = 0
            expr_active = False
            expr_lines  = []
            continue

        if current is None:
            continue

        # -- declaration section: [CONFIG:] and [BR:EXPR:BEGIN/END] only
        if in_sig:
            for m in RE_CONFIG.finditer(line):
                param_m = re.match(r'^\s*(\w+)', line)
                current.configs.append(ConfigRef(
                    scope=m.group(1), key=m.group(2),
                    param=param_m.group(1) if param_m else '?',
                    line_no=line_no,
                ))
            if RE_BR_EXPR_B.search(line):
                expr_active = True
                expr_start  = line_no
                expr_lines  = []
                continue
            if RE_BR_EXPR_E.search(line):
                _save_expr()
                continue
            if expr_active:
                expr_lines.append(line)
            if re.match(r'^\s*BEGIN\b', line, re.IGNORECASE):
                in_sig  = False
                in_body = True
                depth   = 1
            continue

        # -- body section
        if not in_body:
            continue

        # nesting depth (bare END only)
        if re.match(r'^\s*BEGIN\b', line, re.IGNORECASE):
            depth += 1
        if _is_end_block(line):
            depth -= 1
            if depth <= 0:
                in_body = False
                current = None
                _save_expr()
                continue

        # [BR:EXPR:BEGIN]
        if RE_BR_EXPR_B.search(line):
            expr_active = True
            expr_start  = line_no
            expr_lines  = []
            continue

        # [BR:EXPR:END]
        if RE_BR_EXPR_E.search(line):
            _save_expr()
            continue

        # accumulate expr lines
        if expr_active:
            expr_lines.append(line)
            continue

        # [BR:LEAF(entity:attr)]
        m_leaf = RE_BR_LEAF.search(line)
        if m_leaf:
            current.leaves.append(BrLeaf(
                entity=m_leaf.group(1),
                attr=m_leaf.group(2),
                condition=_extract_condition(line),
                line_no=line_no,
            ))

        # [BR:EXPR] inline
        if RE_BR_EXPR.search(line):
            code = re.sub(r'\s*--.*$', '', line).strip()
            if code:
                current.exprs.append(BrExpr(sql=[code], line_no=line_no))

        # [BR:STATE]
        if RE_BR_STATE.search(line):
            code = re.sub(r'\s*--.*$', '', line).strip()
            if code:
                current.states.append(BrState(code=code, line_no=line_no))

        # [BR:FLOW]
        if RE_BR_FLOW.search(line):
            goto_m  = re.search(r'\bGOTO\s+(\w+)', line, re.IGNORECASE)
            label_m = re.match(r'\s*<<(\w+)>>', line)
            cond = ''
            if re.match(r'^\s*(?:IF|ELSIF)\b', line, re.IGNORECASE):
                cond = _extract_condition(line)
            target = (goto_m.group(1) if goto_m
                      else label_m.group(1) if label_m
                      else 'EXIT')
            current.flows.append(BrFlow(target=target, condition=cond, line_no=line_no))

        # [BR:TODO]
        if RE_BR_TODO.search(line):
            code = re.sub(r'\s*--.*$', '', line).strip()
            current.todos.append((code or '?', line_no))

        # local procedure calls — with args: name(...) or without: name;
        call_m = re.match(r'^\s*(\w+)\s*\(', line)
        if not call_m:
            call_m = re.match(r'^\s*(\w+)\s*;', line)
        if call_m and ':=' not in line:
            name = call_m.group(1).lower()
            if name not in KW_SKIP and name != current.name and name not in current.calls:
                current.calls.append(name)

        # pkg-state lateral edge tracking
        for m_w in RE_PKG_WRITE.finditer(line):
            current.pkg_state.append(PkgStateRef(m_w.group(1), 'write', line_no))
        if re.match(r'^\s*(?:IF|ELSIF)\b', line, re.IGNORECASE) and ':=' not in line:
            seen = {s.var for s in current.pkg_state if s.line_no == line_no}
            for m_r in RE_PKG_READ.finditer(line):
                if m_r.group(1) not in seen:
                    current.pkg_state.append(PkgStateRef(m_r.group(1), 'read', line_no))

    return nodes


# ---- emitter -----------------------------------------------------------------

SEP1 = '-' * 60
SEP2 = '=' * 60


def emit_rule(node):
    out = ["\n" + SEP1, f"NODE  {node.name}  (line {node.line_no})", SEP1]

    if node.todos:
        out.append(f"  !! TODO ({len(node.todos)}) -- unresolved debt:")
        for code, ln in node.todos:
            out.append(f"    [TODO@L{ln}]  {code}")

    if node.configs:
        out.append("  CONFIG refs:")
        for c in node.configs:
            out.append(f"    [{c.scope}:{c.key}]  <- param '{c.param}'  (L{c.line_no})")

    if node.exprs:
        out.append("  EXPRESSIONS:")
        for e in node.exprs:
            out.append(f"    [EXPR@L{e.line_no}]")
            for ln in e.sql[:6]:
                out.append(f"      {ln.strip()}")
            if len(e.sql) > 6:
                out.append(f"      ... (+{len(e.sql)-6} lines)")

    if node.leaves:
        out.append("  RULES:")
        groups = {}
        for lf in node.leaves:
            groups.setdefault(f"{lf.entity}.{lf.attr}", []).append(lf)
        for key, lfs in groups.items():
            out.append(f"    RULE {key}")
            for lf in lfs:
                out.append(f"      WHEN  {lf.condition}")
                following = [e for e in node.exprs if e.line_no > lf.line_no]
                if following:
                    e = min(following, key=lambda x: x.line_no)
                    out.append(f"        -> [EXPR@L{e.line_no}]")
                    for ln in e.sql[:4]:
                        out.append(f"           {ln.strip()}")
                    if len(e.sql) > 4:
                        out.append(f"           ... (+{len(e.sql)-4} lines)")

    if node.states:
        out.append("  STATE:")
        for s in node.states:
            out.append(f"    [STATE@L{s.line_no}]  {s.code}")

    if node.flows:
        out.append("  FLOW:")
        for fl in node.flows:
            cond_str = f"  WHEN {fl.condition}" if fl.condition else ""
            out.append(f"    -> {fl.target}{cond_str}  (L{fl.line_no})")

    if node.calls:
        out.append("  CALLS:")
        for c in node.calls:
            out.append(f"    -> {c}")

    writes = [s for s in node.pkg_state if s.direction == 'write']
    reads  = [s for s in node.pkg_state if s.direction == 'read']
    seen_vars: set = set()
    pkg_lines = []
    for s in writes:
        if s.var not in seen_vars:
            pkg_lines.append(f"    WRITE  {s.var}  (L{s.line_no})")
            seen_vars.add(s.var)
    for s in reads:
        if s.var not in seen_vars:
            pkg_lines.append(f"    READ   {s.var}  (L{s.line_no})")
            seen_vars.add(s.var)
    if pkg_lines:
        out.append("  PKG STATE (lateral edges):")
        out.extend(pkg_lines)

    return "\n".join(out)


def emit_edges(nodes):
    all_names = {n.name for n in nodes}
    out = ["\n" + SEP2, "EDGE SUMMARY", SEP2]

    out.append("\n  Call graph:")
    for n in nodes:
        for c in n.calls:
            if c in all_names:
                out.append(f"    {n.name}  ->  {c}")

    writes_map = {}
    reads_map  = {}
    for n in nodes:
        for s in n.pkg_state:
            if s.direction == 'write':
                writes_map.setdefault(s.var, set()).add(n.name)
            else:
                reads_map.setdefault(s.var, set()).add(n.name)

    bridges = []
    for var, writers in writes_map.items():
        if var in reads_map:
            for w in writers:
                for r in reads_map[var]:
                    if w != r and (w, var, r) not in bridges:
                        bridges.append((w, var, r))

    if bridges:
        out.append("\n  Pkg-state lateral edges (invisible to call graph):")
        for w, var, r in bridges:
            out.append(f"    {w}  --[{var}]-->  {r}")
    else:
        out.append("\n  (no pkg-state lateral edges between extracted nodes)")

    todos_total = sum(len(n.todos) for n in nodes)
    if todos_total:
        nodes_with_todos = sum(1 for n in nodes if n.todos)
        out.append(f"\n  !! DEBT: {todos_total} [BR:TODO] across {nodes_with_todos} node(s) -- target: zero")

    return "\n".join(out)


# ---- graph data (JSON for the viewer) ----------------------------------------

def build_graph_data(nodes):
    """Deterministic graph payload consumed by br_graph.html (const RAW).

    Shape: {"nodes":[{id,line,rules,exprs,states,flows}],
            "calls":[{s,t}], "lateral":[{s,t,v}]}
    Same topology the emit_edges() text summary describes.
    """
    all_names = {n.name for n in nodes}

    out_nodes = [
        {"id": n.name, "line": n.line_no,
         "rules": len(n.leaves), "exprs": len(n.exprs),
         "states": len(n.states), "flows": len(n.flows)}
        for n in nodes
    ]

    calls = []
    seen_call = set()
    for n in nodes:
        for c in n.calls:
            if c in all_names and (n.name, c) not in seen_call:
                calls.append({"s": n.name, "t": c})
                seen_call.add((n.name, c))

    writes_map, reads_map = {}, {}
    for n in nodes:
        for s in n.pkg_state:
            target = writes_map if s.direction == 'write' else reads_map
            target.setdefault(s.var, set()).add(n.name)

    lateral, seen_lat = [], set()
    for var, writers in writes_map.items():
        if var in reads_map:
            for w in sorted(writers):
                for r in sorted(reads_map[var]):
                    if w != r and (w, var, r) not in seen_lat:
                        lateral.append({"s": w, "t": r, "v": var})
                        seen_lat.add((w, var, r))

    return {"nodes": out_nodes, "calls": calls, "lateral": lateral}


# ---- entry point -------------------------------------------------------------

def main():
    args = [a for a in sys.argv[1:] if a != '--json']
    json_mode = '--json' in sys.argv
    if not args:
        sys.exit("Usage: br_extractor.py [--json] <src_file> [proc_name ...]")
    src_path = Path(args[0])
    if not src_path.exists():
        sys.exit(f"File not found: {src_path}")

    filter_names = {a.lower() for a in args[1:]}
    src_lines = src_path.read_text(encoding='utf-8').splitlines(keepends=True)
    nodes = parse(src_lines)
    if filter_names:
        nodes = [n for n in nodes if n.name in filter_names]
    if not nodes:
        if json_mode:
            print(json.dumps({"nodes": [], "calls": [], "lateral": []}))
        else:
            print("No annotated nodes found.")
        return

    if json_mode:
        print(json.dumps(build_graph_data(nodes)))
        return

    for node in nodes:
        print(emit_rule(node))
    if len(nodes) > 1:
        print(emit_edges(nodes))
    print(f"\n  {len(nodes)} node(s) extracted.")


if __name__ == '__main__':
    main()
