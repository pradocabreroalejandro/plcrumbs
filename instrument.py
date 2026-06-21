#!/usr/bin/env python3
"""
instrument.py  —  Annotation-driven PL/SQL instrumentation generator.
Usage: python instrument.py <profile> <src_file>
Profiles: debug | otel
"""
import re, sys
from pathlib import Path

PROFILES = ('debug', 'otel', 'otel_debug')


# ── line helpers ─────────────────────────────────────────────────────────────

def _pad(line):
    return ' ' * (len(line) - len(line.lstrip()))

def _into_vars(line):
    """Extract variable names from an INTO clause on this line."""
    m = re.search(r'\bINTO\b\s+([\w ,]+?)(?:\s*--|$)', line, re.IGNORECASE)
    return [v.strip() for v in m.group(1).split(',')] if m else []

def _assign_var(line):
    """Extract left-hand variable from a := assignment on this line."""
    m = re.match(r'\s*(\w+)\s*:=', line)
    return m.group(1) if m else None

def _func_name(line):
    m = re.match(r'\s*FUNCTION\s+(\w+)', line, re.IGNORECASE)
    return m.group(1).lower() if m else None

def _in_params(lines, start):
    """Collect IN parameter names from the function signature at lines[start]."""
    block = ''
    for ln in lines[start:start + 15]:
        block += ln
        if re.search(r'\b(IS|AS)\b', ln, re.IGNORECASE):
            break
    return re.findall(r'(\w+)\s+IN\b', block, re.IGNORECASE)


# ── code renderers ───────────────────────────────────────────────────────────

def _emit(profile, event, **kw):
    pad = _pad(kw.get('ref_line', '    '))

    if event == 'trace_entry':
        fn, params = kw['fn'], kw['params']
        parts = [f"'{fn}'"] + [f"' {p}=' || {p}" for p in params]
        dbg = [f"{pad}DBMS_OUTPUT.PUT_LINE('>>> ' || {' || '.join(parts)});"]
        otel = [f"{pad}l_span_id := PLTelemetry.start_span('{fn}');"]
        otel += [f"{pad}PLTelemetry.attr('{p}', {p});" for p in params]
        if profile == 'debug':      return dbg
        if profile == 'otel':       return otel
        if profile == 'otel_debug': return otel + dbg

    if event == 'trace_exit':
        fn = kw['fn']
        dbg  = [f"{pad}DBMS_OUTPUT.PUT_LINE('<<< {fn}');"]
        otel = [f"{pad}PLTelemetry.end_span('OK');"]
        if profile == 'debug':      return dbg
        if profile == 'otel':       return otel
        if profile == 'otel_debug': return otel + dbg

    if event == 'log_vars':
        vars_ = kw['vars']
        dbg  = [f"{pad}DBMS_OUTPUT.PUT_LINE('{v}=' || {v});" for v in vars_]
        otel = [f"{pad}PLTelemetry.attr('{v}', {v});" for v in vars_]
        if profile == 'debug':      return dbg
        if profile == 'otel':       return otel
        if profile == 'otel_debug': return otel + dbg

    if event == 'span_decl':
        if profile in ('otel', 'otel_debug'):
            return [f"{pad}  l_span_id  VARCHAR2(64);"]  # +2 to match declaration indent vs BEGIN
        return []

    return []


# ── main processor ───────────────────────────────────────────────────────────

def instrument(src_lines, profile):
    out                = []
    current_func       = None
    func_params        = []
    trace_count        = 0
    in_decl_block      = False  # between IS and BEGIN of a function
    pending_after_semi = []     # debug lines waiting for the statement's closing ;
    last_assign_var    = None   # tracks l_x := CASE ... multi-line assignments

    for i, line in enumerate(src_lines):

        # ── track function context ───────────────────────────────────────
        fn = _func_name(line)
        if fn:
            current_func       = fn
            func_params        = _in_params(src_lines, i)
            trace_count        = 0
            in_decl_block      = False
            pending_after_semi = []
            last_assign_var    = None

        if current_func and re.match(r'\s*IS\s*$', line, re.IGNORECASE):
            in_decl_block = True

        # ── inject span declaration just before function BEGIN (otel) ────
        if in_decl_block and re.match(r'\s*BEGIN\b', line, re.IGNORECASE):
            in_decl_block = False
            for gen in _emit(profile, 'span_decl', ref_line=line):
                out.append(gen + '\n')

        # ── track last := variable (for multi-line CASE assignments) ─────
        if ':=' in line:
            v = _assign_var(line)
            if v:
                last_assign_var = v

        # ── breadcrumbs ──────────────────────────────────────────────────
        if '-- [LOG:trace]' in line:
            trace_count += 1
            out.append(line)
            event = 'trace_entry' if trace_count == 1 else 'trace_exit'
            for gen in _emit(profile, event,
                             fn=current_func, params=func_params, ref_line=line):
                out.append(gen + '\n')

        elif '-- [LOG:debug]' in line:
            out.append(line)
            vars_ = (_into_vars(line)
                     or ([_assign_var(line)] if _assign_var(line) else [])
                     or ([last_assign_var]   if last_assign_var   else []))
            gen_lines = [g + '\n' for g in _emit(profile, 'log_vars', vars=vars_, ref_line=line)]
            if ';' in line:
                # single-line statement — emit immediately
                out.extend(gen_lines)
            else:
                # multi-line statement (e.g. SELECT…INTO…FROM) — defer until ;
                pending_after_semi.extend(gen_lines)

        else:
            out.append(line)

        # ── flush deferred debug lines after statement terminator ────────
        # skip lines that are pure comments — a ; inside -- text is not a terminator
        if ';' in line and not line.strip().startswith('--'):
            if pending_after_semi:
                out.extend(pending_after_semi)
                pending_after_semi.clear()
            last_assign_var = None

    return out


# ── entry point ──────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) != 3:
        sys.exit(f'Usage: instrument.py <{"|".join(PROFILES)}> <src_file>')

    profile   = sys.argv[1].lower()
    src_path  = Path(sys.argv[2])

    if profile not in PROFILES:
        sys.exit(f"Unknown profile '{profile}'. Choose: {', '.join(PROFILES)}")
    if not src_path.exists():
        sys.exit(f'File not found: {src_path}')

    src_lines = src_path.read_text(encoding='utf-8').splitlines(keepends=True)
    out_lines = instrument(src_lines, profile)

    out_dir  = src_path.parent / profile
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / src_path.name
    out_path.write_text(''.join(out_lines), encoding='utf-8')
    print(f'Generated: {out_path}')


if __name__ == '__main__':
    main()
