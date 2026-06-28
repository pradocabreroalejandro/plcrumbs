CREATE OR REPLACE PACKAGE pkg_solar_monitor AS
/*
  Solar plant health monitoring and alarm classification.

  evaluate_plant_health() is the orchestrator: it loads the plant context,
  walks every string reading in the window, classifies per-string alarms,
  applies thermal derating and checks inverter status, then aggregates a
  plant-level health summary.

  This is the [BR:*] reference package for plcrumbs -- annotated so the
  business-rule extractor can derive the call graph, the package-state
  lateral edges, and orphan (dead-code) detection without any LLM.
*/

  -- Orchestrator. Returns a JSON health summary for one installation.
  FUNCTION evaluate_plant_health(
    p_installation_id IN NUMBER,
    p_window_mins     IN NUMBER DEFAULT 15
  ) RETURN CLOB;

  -- Clear-sky index. NOTE: superseded by pkg_solar_irradiance.clear_sky_index.
  -- Kept in the spec but nothing in this package calls it -- see the graph.
  FUNCTION calculate_irradiance_index(
    p_ghi_w_m2       IN NUMBER,
    p_clear_sky_w_m2 IN NUMBER
  ) RETURN NUMBER;

END pkg_solar_monitor;
/
