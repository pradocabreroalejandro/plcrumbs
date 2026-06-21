CREATE OR REPLACE PACKAGE BODY pkg_solar_energy AS

  c_pkg  CONSTANT VARCHAR2(30) := 'PKG_SOLAR_ENERGY';

  -- -------------------------------------------------------------------------
  FUNCTION get_panel_strings(
    p_installation_id IN NUMBER
  ) RETURN CLOB
  IS
    l_fn             CONSTANT VARCHAR2(30) := 'GET_PANEL_STRINGS';

    l_tot_panels     NUMBER;
    l_panel_voc      NUMBER;   -- open-circuit voltage per panel  (V)
    l_panel_isc      NUMBER;   -- short-circuit current per panel (A)
    l_panels_per_str NUMBER;   -- panels wired in series per string
    l_string_count   NUMBER;
    l_string_voc     NUMBER;   -- string Voc  = panels_per_string * panel_voc
    l_string_power   NUMBER;   -- peak power  = string_voc * panel_isc  (W)

    l_root           JSON_OBJECT_T;
    l_strings_arr    JSON_ARRAY_T  := JSON_ARRAY_T();
    l_str_obj        JSON_OBJECT_T;
  BEGIN
    -- [LOG:trace]

    SELECT tot_panels, panel_voc_v, panel_isc_a, panels_per_string
    INTO   l_tot_panels, l_panel_voc, l_panel_isc, l_panels_per_str -- [LOG:debug]
    FROM  (
      SELECT 1 AS installation_id, 24 AS tot_panels, 41.20 AS panel_voc_v,  9.86 AS panel_isc_a, 4 AS panels_per_string FROM DUAL UNION ALL
      SELECT 2,                    36,               38.50,                 10.20,               6                      FROM DUAL UNION ALL
      SELECT 3,                    12,               40.10,                  9.50,               4                      FROM DUAL
    )
    WHERE installation_id = p_installation_id;

    l_string_count := FLOOR(l_tot_panels / l_panels_per_str);
    l_string_voc   := l_panels_per_str * l_panel_voc;    -- [LOG:debug]
    l_string_power := l_string_voc * l_panel_isc;        -- [LOG:debug]

    l_root := JSON_OBJECT_T();
    l_root.put('installation_id',   p_installation_id);
    l_root.put('total_panels',      l_tot_panels);
    l_root.put('string_count',      l_string_count);
    l_root.put('panels_per_string', l_panels_per_str);

    FOR i IN 1 .. l_string_count LOOP
      l_str_obj := JSON_OBJECT_T();
      l_str_obj.put('string_id',   i);
      l_str_obj.put('voc_volts',   ROUND(l_string_voc,   2));
      l_str_obj.put('isc_amps',    ROUND(l_panel_isc,    2));
      l_str_obj.put('peak_watts',  ROUND(l_string_power, 2));
      l_strings_arr.append(l_str_obj);
    END LOOP;

    l_root.put('strings', l_strings_arr);

    -- [LOG:trace]
    RETURN l_root.to_clob;

  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20001,
        c_pkg || '.' || l_fn || ': installation_id ' || p_installation_id || ' not found');
    WHEN OTHERS THEN
      RAISE;
  END get_panel_strings;

  -- -------------------------------------------------------------------------
  FUNCTION get_string_health(
    p_installation_id IN NUMBER,
    p_string_id       IN NUMBER
  ) RETURN VARCHAR2
  IS
    l_fn             CONSTANT VARCHAR2(30) := 'GET_STRING_HEALTH';

    l_expected_voc   NUMBER;
    l_measured_voc   NUMBER;
    l_deviation_pct  NUMBER;
    l_status         VARCHAR2(10);
  BEGIN
    -- [LOG:trace]

    SELECT expected_voc, measured_voc
    INTO   l_expected_voc, l_measured_voc -- [LOG:debug]
    FROM  (
      -- Simulates a live telemetry read; real version hits an inverter/SCADA table
      SELECT 164.80 AS expected_voc,
             CASE p_string_id
               WHEN 1 THEN 163.90
               WHEN 2 THEN 158.20
               ELSE        164.50
             END    AS measured_voc
      FROM DUAL
    );

    l_deviation_pct := ABS(l_measured_voc - l_expected_voc) / l_expected_voc * 100; -- [LOG:debug]

    l_status := CASE
                  WHEN l_deviation_pct <= 2  THEN 'OK'
                  WHEN l_deviation_pct <= 10 THEN 'WARN'
                  ELSE                            'FAULT'
                END; -- [LOG:debug]

    -- [LOG:trace]
    RETURN l_status;

  EXCEPTION
    WHEN OTHERS THEN
      RAISE;
  END get_string_health;

END pkg_solar_energy;
/
