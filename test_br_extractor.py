#!/usr/bin/env python3
"""Unit tests for br_extractor.py.

Run with:  python -m unittest test_br_extractor  (or)  python test_br_extractor.py
"""
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

# Make the module importable when run from any directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import br_extractor as br


# ---- helpers -----------------------------------------------------------------

def parse_text(src: str):
    """Parse a PL/SQL snippet (as string) and return nodes."""
    lines = src.splitlines(keepends=True)
    return br.parse(lines)


PROC_SIMPLE = """\
PROCEDURE simple IS
BEGIN
  -- [BR:TODO] unresolved
  x := 1; -- [BR:STATE]
END simple;
"""


PROC_LEAF_ASSERT_EXPR = """\
PROCEDURE check_value(
  p_val IN NUMBER -- [CONFIG:cfg:threshold]
) IS
BEGIN
  -- [BR:ASSERT(entity:attr)]
  IF p_val IS NULL THEN
    RETURN; -- [BR:EXIT]
  END IF;

  -- [BR:EXPR:BEGIN]
  l_result := p_val * 2;
  -- [BR:EXPR:END]

  IF p_val > 10 THEN -- [BR:LEAF(entity:big)]
    l_result := 100; -- [BR:EXPR]
  END IF;
END check_value;
"""


PROC_FLOW_GOTO = """\
PROCEDURE flow_demo IS
BEGIN
  IF x > 5 THEN -- [BR:FLOW]
    GOTO done; -- [BR:FLOW]
  END IF;
  <<done>> -- [BR:FLOW]
  NULL;
END flow_demo;
"""


PROC_CALL = """\
PROCEDURE caller IS
BEGIN
  helper_proc(param1);
  standalone_proc;
  standalone_proc;  -- duplicate, should not be added again
END caller;

PROCEDURE helper_proc(p1 IN NUMBER) IS
BEGIN
  NULL;
END helper_proc;

PROCEDURE standalone_proc IS
BEGIN
  NULL;
END standalone_proc;
"""


PKGVAR_SRC = """\
CREATE OR REPLACE PACKAGE BODY pkg_demo AS
  g_shared   NUMBER; -- [BR:PKGVAR]
  g_private  NUMBER;

PROCEDURE writer IS
BEGIN
  g_shared := 1; -- [BR:STATE]
END writer;

PROCEDURE reader IS
BEGIN
  l_local := g_shared + 1; -- [BR:STATE]
END reader;
END pkg_demo;
"""


PROC_EXCEPTION = """\
PROCEDURE with_handler IS
BEGIN
  NULL;
EXCEPTION
  WHEN OTHERS THEN
    RAISE;
END with_handler;
"""


PROC_FUNC_SIG = """\
FUNCTION compute(
  p_x IN NUMBER,
  p_y OUT VARCHAR2
) RETURN NUMBER IS
BEGIN
  RETURN p_x;
END compute;
"""


# ---- test cases ---------------------------------------------------------------

class TestRegexes(unittest.TestCase):
    def test_regex_leaf(self):
        m = br.RE_BR_LEAF.search("-- [BR:LEAF(order:amount)]")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), 'order')
        self.assertEqual(m.group(2), 'amount')

    def test_regex_assert(self):
        m = br.RE_BR_ASSERT.search("-- [BR:ASSERT(order:amount)]")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), 'amount')

    def test_regex_expr_begin_end(self):
        self.assertTrue(br.RE_BR_EXPR_B.search('-- [BR:EXPR:BEGIN]'))
        self.assertTrue(br.RE_BR_EXPR_E.search('-- [BR:EXPR:END]'))

    def test_regex_expr_inline(self):
        self.assertTrue(br.RE_BR_EXPR.search('  x := 1; -- [BR:EXPR]'))
        self.assertFalse(br.RE_BR_EXPR.search('  -- [BR:EXPR:BEGIN]'))

    def test_regex_config(self):
        m = br.RE_CONFIG.search('-- [CONFIG:scope:key]')
        self.assertEqual(m.group(1), 'scope')
        self.assertEqual(m.group(2), 'key')

    def test_regex_call_annotation(self):
        m = br.RE_BR_CALL.search('-- [BR:CALL:dynamic_target]')
        self.assertEqual(m.group(1), 'dynamic_target')

    def test_regex_pkgvar(self):
        self.assertTrue(br.RE_BR_PKGVAR.search('  g_x NUMBER; -- [BR:PKGVAR]'))


class TestHelpers(unittest.TestCase):
    def test_extract_condition_if(self):
        cond = br._extract_condition('  IF x > 10 THEN -- comment')
        self.assertEqual(cond, 'x > 10')

    def test_extract_condition_elsif(self):
        cond = br._extract_condition('  ELSIF y = 5 THEN')
        self.assertEqual(cond, 'y = 5')

    def test_extract_condition_fallback(self):
        cond = br._extract_condition('  x := 1;')
        self.assertEqual(cond, 'x := 1;')

    def test_is_end_block_bare(self):
        self.assertTrue(br._is_end_block('END proc;'))

    def test_is_end_block_if(self):
        self.assertFalse(br._is_end_block('END IF;'))

    def test_is_end_block_loop(self):
        self.assertFalse(br._is_end_block('END LOOP;'))

    def test_is_call_skip_keyword(self):
        self.assertTrue(br._is_call_skip('select'))
        self.assertFalse(br._is_call_skip('my_proc'))


class TestParserBasic(unittest.TestCase):
    def test_empty_source(self):
        self.assertEqual(parse_text(''), [])

    def test_no_annotations(self):
        nodes = parse_text('PROCEDURE p IS BEGIN NULL; END p;')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].name, 'p')
        self.assertEqual(len(nodes[0].leaves), 0)

    def test_procedure_name(self):
        nodes = parse_text(PROC_SIMPLE)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].name, 'simple')

    def test_todo_capture(self):
        nodes = parse_text(PROC_SIMPLE)
        self.assertEqual(len(nodes[0].todos), 1)

    def test_state_capture(self):
        nodes = parse_text(PROC_SIMPLE)
        self.assertEqual(len(nodes[0].states), 1)
        self.assertIn('x', nodes[0].states[0].code)

    def test_exception_detection(self):
        nodes = parse_text(PROC_EXCEPTION)
        self.assertTrue(nodes[0].has_exception)
        self.assertGreater(nodes[0].exception_line, 0)

    def test_no_exception(self):
        nodes = parse_text(PROC_SIMPLE)
        self.assertFalse(nodes[0].has_exception)


class TestParserSignatures(unittest.TestCase):
    def test_function_sig_params(self):
        nodes = parse_text(PROC_FUNC_SIG)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].name, 'compute')
        self.assertIn('p_x', nodes[0].params_in)
        self.assertIn('p_y', nodes[0].params_out)


class TestParserCrumbFamilies(unittest.TestCase):
    def test_config_capture(self):
        nodes = parse_text(PROC_LEAF_ASSERT_EXPR)
        self.assertEqual(len(nodes[0].configs), 1)
        self.assertEqual(nodes[0].configs[0].scope, 'cfg')
        self.assertEqual(nodes[0].configs[0].key, 'threshold')

    def test_assert_capture(self):
        nodes = parse_text(PROC_LEAF_ASSERT_EXPR)
        self.assertEqual(len(nodes[0].asserts), 1)
        self.assertEqual(nodes[0].asserts[0].entity, 'entity')

    def test_leaf_capture(self):
        nodes = parse_text(PROC_LEAF_ASSERT_EXPR)
        self.assertEqual(len(nodes[0].leaves), 1)
        self.assertEqual(nodes[0].leaves[0].entity, 'entity')
        self.assertEqual(nodes[0].leaves[0].attr, 'big')

    def test_exit_capture(self):
        nodes = parse_text(PROC_LEAF_ASSERT_EXPR)
        self.assertEqual(len(nodes[0].exits), 1)

    def test_expr_block_capture(self):
        nodes = parse_text(PROC_LEAF_ASSERT_EXPR)
        self.assertGreaterEqual(len(nodes[0].exprs), 1)
        joined = '\n'.join(nodes[0].exprs[0].sql)
        self.assertIn('l_result', joined)

    def test_expr_inline_capture(self):
        nodes = parse_text(PROC_LEAF_ASSERT_EXPR)
        inline = [e for e in nodes[0].exprs if 'l_result := 100' in '\n'.join(e.sql)]
        self.assertTrue(inline, "inline [BR:EXPR] should be captured")

    def test_flow_goto(self):
        nodes = parse_text(PROC_FLOW_GOTO)
        flows = nodes[0].flows
        # Source has three [BR:FLOW] crumbs: IF guard (EXIT), GOTO (done), label (done).
        self.assertEqual(len(flows), 3)
        self.assertEqual(flows[0].target, 'EXIT')
        self.assertEqual(flows[1].target, 'done')
        self.assertEqual(flows[2].target, 'done')

    def test_call_detection(self):
        nodes = parse_text(PROC_CALL)
        caller = next(n for n in nodes if n.name == 'caller')
        self.assertIn('helper_proc', caller.calls)
        self.assertIn('standalone_proc', caller.calls)
        # No duplicates
        self.assertEqual(caller.calls.count('standalone_proc'), 1)

    def test_call_skip_keywords(self):
        nodes = parse_text(
            "PROCEDURE p IS BEGIN SELECT x FROM t; NULL; END p;")
        self.assertEqual(len(nodes[0].calls), 0)


class TestPkgVarLateral(unittest.TestCase):
    def test_pkgvar_whitelist(self):
        nodes = parse_text(PKGVAR_SRC)
        writer = next(n for n in nodes if n.name == 'writer')
        reader = next(n for n in nodes if n.name == 'reader')

        writes = {s.var for s in writer.pkg_state if s.direction == 'write'}
        self.assertIn('g_shared', writes)

        reads = {s.var for s in reader.pkg_state if s.direction == 'read'}
        self.assertIn('g_shared', reads)

    def test_lateral_edge_built(self):
        nodes = parse_text(PKGVAR_SRC)
        graph = br.build_graph_data(nodes)
        lat = [(e['s'], e['t'], e['v']) for e in graph['lateral']]
        self.assertIn(('writer', 'reader', 'g_shared'), lat)

    def test_non_pkgvar_ignored(self):
        # g_private has no [BR:PKGVAR] crumb -> should not appear
        nodes = parse_text(PKGVAR_SRC)
        all_refs = {s.var for n in nodes for s in n.pkg_state}
        self.assertNotIn('g_private', all_refs)


class TestGraphData(unittest.TestCase):
    def test_graph_shape(self):
        nodes = parse_text(PKGVAR_SRC)
        data = br.build_graph_data(nodes)
        self.assertIn('nodes', data)
        self.assertIn('calls', data)
        self.assertIn('lateral', data)

    def test_node_fields(self):
        nodes = parse_text(PROC_LEAF_ASSERT_EXPR)
        data = br.build_graph_data(nodes)
        nd = data['nodes'][0]
        for key in ('id', 'line', 'rules', 'asserts', 'exprs', 'expr_lines',
                    'states', 'flows', 'exits', 'configs', 'has_exception',
                    'expr_preview'):
            self.assertIn(key, nd)

    def test_node_counts(self):
        nodes = parse_text(PROC_LEAF_ASSERT_EXPR)
        data = br.build_graph_data(nodes)
        nd = data['nodes'][0]
        self.assertEqual(nd['rules'], 1)
        self.assertEqual(nd['asserts'], 1)
        self.assertEqual(nd['configs'], 1)
        self.assertGreaterEqual(nd['exprs'], 1)

    def test_call_edge(self):
        nodes = parse_text(PROC_CALL)
        data = br.build_graph_data(nodes)
        edges = [(e['s'], e['t']) for e in data['calls']]
        self.assertIn(('caller', 'helper_proc'), edges)
        self.assertIn(('caller', 'standalone_proc'), edges)

    def test_no_duplicate_calls(self):
        nodes = parse_text(PROC_CALL)
        data = br.build_graph_data(nodes)
        edges = [(e['s'], e['t']) for e in data['calls']]
        self.assertEqual(edges.count(('caller', 'standalone_proc')), 1)

    def test_dynamic_calls(self):
        src = """\
PROCEDURE dyn IS
BEGIN
  EXECUTE IMMEDIATE 'do_something'; -- [BR:CALL:dynamic_proc]
END dyn;
"""
        nodes = parse_text(src)
        data = br.build_graph_data(nodes)
        self.assertIn('dynamic_calls', data)
        dyn = [(e['s'], e['t']) for e in data['dynamic_calls']]
        self.assertIn(('dyn', 'dynamic_proc'), dyn)

    def test_empty_graph(self):
        data = br.build_graph_data([])
        self.assertEqual(data['nodes'], [])
        self.assertEqual(data['calls'], [])
        self.assertEqual(data['lateral'], [])

    def test_expr_preview(self):
        src = """\
PROCEDURE p IS
BEGIN
  -- [BR:EXPR:BEGIN]
  l_x := a + b;
  l_y := c * d;
  -- [BR:EXPR:END]
END p;
"""
        nodes = parse_text(src)
        data = br.build_graph_data(nodes)
        self.assertTrue(data['nodes'][0]['expr_preview'])

    def test_no_expr_preview(self):
        nodes = parse_text(PROC_SIMPLE)
        data = br.build_graph_data(nodes)
        self.assertEqual(data['nodes'][0]['expr_preview'], "")


class TestEmitter(unittest.TestCase):
    def test_emit_rule_contains_name(self):
        nodes = parse_text(PROC_SIMPLE)
        out = br.emit_rule(nodes[0])
        self.assertIn('simple', out)

    def test_emit_rule_todo(self):
        nodes = parse_text(PROC_SIMPLE)
        out = br.emit_rule(nodes[0])
        self.assertIn('TODO', out)

    def test_emit_rule_exception(self):
        nodes = parse_text(PROC_EXCEPTION)
        out = br.emit_rule(nodes[0])
        self.assertIn('EXCEPTION', out)

    def test_emit_rule_configs(self):
        nodes = parse_text(PROC_LEAF_ASSERT_EXPR)
        out = br.emit_rule(nodes[0])
        self.assertIn('CONFIG refs:', out)
        self.assertIn('cfg', out)

    def test_emit_edges_summary(self):
        nodes = parse_text(PKGVAR_SRC)
        out = br.emit_edges(nodes)
        self.assertIn('EDGE SUMMARY', out)

    def test_emit_edges_lateral(self):
        nodes = parse_text(PKGVAR_SRC)
        out = br.emit_edges(nodes)
        self.assertIn('g_shared', out)

    def test_emit_edges_calls(self):
        nodes = parse_text(PROC_CALL)
        out = br.emit_edges(nodes)
        self.assertIn('helper_proc', out)


class TestMainCli(unittest.TestCase):
    def _run(self, args):
        old = sys.argv
        sys.argv = ['br_extractor.py'] + args
        buf = io.StringIO()
        rc = None
        try:
            with redirect_stdout(buf):
                br.main()
        except SystemExit as e:
            rc = e.code
        finally:
            sys.argv = old
        return buf.getvalue(), rc

    def test_json_mode_output(self):
        src = PROC_LEAF_ASSERT_EXPR
        p = Path('/tmp/_br_test_src.sql')
        p.write_text(src, encoding='utf-8')
        out, _ = self._run(['--json', str(p)])
        data = json.loads(out)
        self.assertIn('nodes', data)
        self.assertGreaterEqual(len(data['nodes']), 1)

    def test_text_mode_output(self):
        src = PROC_SIMPLE
        p = Path('/tmp/_br_test_src.sql')
        p.write_text(src, encoding='utf-8')
        out, _ = self._run([str(p)])
        self.assertIn('NODE', out)

    def test_filter_by_name(self):
        src = PROC_CALL
        p = Path('/tmp/_br_test_src.sql')
        p.write_text(src, encoding='utf-8')
        out, _ = self._run(['--json', str(p), 'caller'])
        data = json.loads(out)
        names = {n['id'] for n in data['nodes']}
        self.assertEqual(names, {'caller'})

    def test_no_nodes_text(self):
        p = Path('/tmp/_br_test_src.sql')
        p.write_text("-- nothing here\n", encoding='utf-8')
        out, _ = self._run([str(p)])
        self.assertIn('No annotated nodes found', out)

    def test_file_not_found(self):
        _, rc = self._run(['/nonexistent/file.sql'])
        self.assertIsNotNone(rc)


if __name__ == '__main__':
    unittest.main()