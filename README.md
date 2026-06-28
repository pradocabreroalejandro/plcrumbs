# plcrumbs

Annotate once. Render anything.

plcrumbs is a lightweight annotation framework for PL/SQL. You add minimal breadcrumb comments to your source. Deterministic parsers read those comments and generate whatever you need — instrumented code, business rule graphs, LLM context — without touching the source.

The source is always the single source of truth. Generated artifacts are never edited by hand.

---

## The architecture

```
annotated source  (.pkb / .sql)
    │
    ├── [LOG:*] annotations ──► instrument.py ──► debug / otel / otel_debug builds
    │
    └── [BR:*]  annotations ──► br_extractor.py ──► business rule graph + LLM index
```

Two annotation families. Two parsers. Multiple artifacts. All deterministic, all re-runnable, zero LLM cost in the generation step.

---

## Annotation families

### `LOG` — observability

| Annotation | Where | What it marks |
|---|---|---|
| `-- [LOG:trace]` | first line in a function body | entry point — logs name + IN params |
| `-- [LOG:trace]` | last line before RETURN/END | exit point — closes span / logs `<<<` |
| `-- [LOG:debug]` | after `SELECT ... INTO` | captures all INTO variables |
| `-- [LOG:debug]` | after `:=` assignment | captures the assigned variable |

### `BR` — business rules

| Annotation | What it marks |
|---|---|
| `-- [BR:LEAF(entity:attr)]` | decision point (IF / CASE condition) |
| `-- [BR:EXPR:BEGIN]` … `-- [BR:EXPR:END]` | multi-line block that produces a value |
| `-- [BR:EXPR]` | inline expression (single line) |
| `-- [BR:STATE]` | flow state — flag, sentinel, counter |
| `-- [BR:FLOW]` | control transfer — GOTO, label, routing |
| `-- [CONFIG:scope:key]` | IN parameter anchored to a config node |
| `-- [BR:TODO]` | unclassified — counted and listed, never graphed |

Breadcrumbs never contain prose. The code is the explanation; the crumb marks where something happens. This keeps annotations honest — the code will contradict a lying crumb immediately.

---

## Tools

### `instrument.py` — observability generator

Reads `LOG` annotations and writes profile-specific instrumented copies.

```bash
python instrument.py <profile> <src_file>

python instrument.py debug      src/pkg_solar_energy.pkb
python instrument.py otel       src/pkg_solar_energy.pkb
python instrument.py otel_debug src/pkg_solar_energy.pkb
```

| Profile | Output |
|---|---|
| `debug` | `DBMS_OUTPUT.PUT_LINE` per annotation |
| `otel` | `PLTelemetry` spans and attributes |
| `otel_debug` | both combined |

Output lands in `src/<profile>/`. Generated files are artifacts — never edit them directly.

### `br_extractor.py` — business rule extractor

Reads `BR` and `CONFIG` annotations and extracts a structured rule graph per procedure.

```bash
python br_extractor.py <sql_file> [proc_name ...]

# full package
python br_extractor.py src/pkg_solar_monitor.pkb > br_rules_full.txt

# single procedure
python br_extractor.py src/pkg_solar_monitor.pkb evaluate_plant_health

# graph data for the viewer (deterministic JSON)
python br_extractor.py --json src/pkg_solar_monitor.pkb > graph_data.json
```

Output per procedure:
- IN/OUT parameters
- CONFIG anchors
- LEAF decision points with conditions
- EXPR blocks (value producers)
- STATE markers
- FLOW transfers
- Outgoing calls (direct + parameterless)
- Package-state lateral edges (pkg variables shared across procedures)
- TODO debt count

The extractor also emits a full edge summary: call graph + lateral dependencies + orphan detection.

### Graph visualization

`br_graph.html` renders the full call graph interactively (D3.js, no server needed). Open in a browser.

- Node size proportional to total annotations
- Color by role: orchestrator, hub, entry point, standard, empty wrapper
- Call edges (solid) and lateral pkg-state edges (dashed, shown per node on click)
- Hover tooltips: LEAF / EXPR / STATE / FLOW counts per procedure

---

## Example — instrumentation

Source (`src/pkg_solar_energy.pkb`):

```sql
FUNCTION get_panel_strings(p_installation_id IN NUMBER) RETURN CLOB IS
  ...
BEGIN
  -- [LOG:trace]

  SELECT tot_panels, panel_voc_v, panel_isc_a
  INTO   l_tot_panels, l_panel_voc, l_panel_isc  -- [LOG:debug]
  FROM   ...;

  l_string_voc := l_panels_per_str * l_panel_voc;  -- [LOG:debug]

  -- [LOG:trace]
  RETURN l_result;
END;
```

Generated (`src/otel/pkg_solar_energy.pkb`):

```sql
  l_span_id := PLTelemetry.start_span('get_panel_strings');
  PLTelemetry.attr('p_installation_id', p_installation_id);

  SELECT tot_panels, panel_voc_v, panel_isc_a
  INTO   l_tot_panels, l_panel_voc, l_panel_isc
  FROM   ...;
  PLTelemetry.attr('l_tot_panels', l_tot_panels);
  PLTelemetry.attr('l_panel_voc',  l_panel_voc);
  PLTelemetry.attr('l_panel_isc',  l_panel_isc);

  l_string_voc := l_panels_per_str * l_panel_voc;
  PLTelemetry.attr('l_string_voc', l_string_voc);

  PLTelemetry.end_span('OK');
  RETURN l_result;
```

## Example — business rules

Source (`src/pkg_solar_monitor.pkb`):

```sql
IF v_deviation_pct >= v_critical_threshold_pct THEN    -- [BR:FLOW]
    GOTO escalate;                                      -- [BR:FLOW]
ELSIF v_deviation_pct >= v_alarm_threshold_pct THEN     -- [BR:LEAF(alarm:warn_band)]
    v_alarm_severity := 'WARN';                         -- [BR:STATE]
    raise_plant_alarm(p_string_id, 'WARN');
END IF;
```

Extracted:

```
[FLOW]  -> escalate          L177  WHEN v_deviation_pct >= v_critical_threshold_pct
[LEAF]  alarm:warn_band      L180  v_deviation_pct >= v_alarm_threshold_pct
[STATE] v_alarm_severity := 'WARN'   L181
```

---

## Folder structure

```
plcrumbs/
  instrument.py              ← observability generator  (LOG family)
  br_extractor.py            ← business rule extractor  (BR family)
  br_graph.html              ← interactive graph viewer
  br_rules_full.txt          ← extractor output (full package)
  graph_data.json            ← graph data for the viewer
  src/
    pkg_solar_energy.pks
    pkg_solar_energy.pkb     ← source of truth (LOG annotations)
    pkg_solar_monitor.pks
    pkg_solar_monitor.pkb    ← source of truth (BR annotations, 8 procedures)
    debug/                   ← generated
    otel/                    ← generated
    otel_debug/              ← generated
```

---

## Requirements

- Python 3.8+, no external dependencies
- OTel profile requires [PLTelemetry](https://github.com/pradocabreroalejandro/pltelemetry) in the target schema
- Graph viewer requires a browser with JavaScript enabled

---

## Design principles

**The source is always deployable as-is.** Breadcrumbs are valid SQL comments. Remove the generator and the code still compiles and runs — just without instrumentation or graph extraction.

**Generated files are artifacts, not source.** They live in the repo and are committable, but `instrument.py` is what you maintain. If an output needs to change, change the source or the generator.

**Annotations are falsifiable.** A breadcrumb that lies about what the code does will be contradicted by the code itself. No prose, no paraphrasing — crumbs mark locations, code provides meaning.

**Vocabulary is earned, not reserved.** New annotation types are added when a real case demands them, not speculatively. The current set (7 BR types + 2 LOG types) covers the solar packages in this repo — and has held up on much larger production packages — without needing extension.
