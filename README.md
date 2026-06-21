# plcrumbs

Annotation-driven instrumentation generator for PL/SQL. Keep your source clean; let a script generate the observability layer.

## The idea

Add minimal breadcrumb comments to your PL/SQL source. Run `instrument.py` with a profile. Get an instrumented version in a separate folder. The source never changes.

```sql
-- /src/pkg_solar_energy.pkb  ← source of truth, always clean

FUNCTION get_panel_strings(p_installation_id IN NUMBER) RETURN CLOB IS
  ...
BEGIN
  -- [LOG:trace]                                              ← entry point

  SELECT tot_panels, panel_voc_v, panel_isc_a
  INTO   l_tot_panels, l_panel_voc, l_panel_isc  -- [LOG:debug]
  FROM   ...;

  l_string_voc := l_panels_per_str * l_panel_voc;  -- [LOG:debug]

  -- [LOG:trace]                                              ← exit point
  RETURN l_result;
END;
```

## Breadcrumbs

| Annotation | Where | What it marks |
|---|---|---|
| `-- [LOG:trace]` | first in a function | entry — logs function name + IN params |
| `-- [LOG:trace]` | last in a function | exit — closes span / logs `<<<` |
| `-- [LOG:debug]` | after `SELECT ... INTO` | captures all INTO variables |
| `-- [LOG:debug]` | after `:=` assignment | captures the assigned variable |

## Profiles

| Profile | Output |
|---|---|
| `debug` | `DBMS_OUTPUT.PUT_LINE` for each annotation |
| `otel` | `PLTelemetry` spans and attributes |
| `otel_debug` | both combined |

## Usage

```bash
python instrument.py <profile> <src_file>

# examples
python instrument.py debug      src/pkg_solar_energy.pkb
python instrument.py otel       src/pkg_solar_energy.pkb
python instrument.py otel_debug src/pkg_solar_energy.pkb
```

Output lands in `src/<profile>/pkg_solar_energy.pkb`. Generated files are artifacts — never edit them by hand.

## Folder structure

```
plcrumbs/
  instrument.py          ← generator
  src/
    pkg_solar_energy.pks
    pkg_solar_energy.pkb ← source of truth
    debug/               ← generated
    otel/                ← generated
    otel_debug/          ← generated
```

## Requirements

- Python 3.8+
- No external dependencies
- OTel profile requires [PLTelemetry](https://github.com/pradocabreroalejandro/pltelemetry) installed in the target schema

## Notes

- The source compiles and runs as-is without the generator — breadcrumbs are valid SQL comments
- The generator is deterministic: same input, same output
- Multi-line `SELECT INTO` statements are handled correctly — debug calls are emitted after the closing `;`, not inline
- Semicolons inside `--` comments do not trigger premature flushing
