CREATE OR REPLACE PACKAGE BODY pkg_solar_monitor AS

  c_pkg  CONSTANT VARCHAR2(30) := 'PKG_SOLAR_MONITOR';

  -- ---- package state -------------------------------------------------------
  -- These v_* package variables are the lateral edges of the graph: written
  -- in one procedure, read in another. The call graph never shows them; the
  -- extractor surfaces them as dashed dependencies.
  v_plant_id                NUMBER;
  v_window_mins             NUMBER;
  v_expected_voc            NUMBER;   -- baseline string Voc (V)
  v_temp_coeff              NUMBER;   -- Voc temperature coefficient (%/degC)
  v_ambient_temp            NUMBER;   -- ambient temperature (degC)
  v_inverter_count          NUMBER;
  v_alarm_threshold_pct     NUMBER;   -- warn band deviation (%)
  v_critical_threshold_pct  NUMBER;   -- critical band deviation (%)

  v_string_voc              NUMBER;   -- last measured string Voc (V)
  v_deviation_pct           NUMBER;   -- last string deviation vs baseline (%)
  v_derate_factor           NUMBER;   -- thermal derate multiplier (0..1)
  v_alarm_severity          VARCHAR2(10);
  v_alarm_count             NUMBER;
  v_inverter_faults         NUMBER;
  v_plant_status            VARCHAR2(12);


  -- ==========================================================================
  -- init_monitor_context -- loads plant config into package state
  -- ==========================================================================
  PROCEDURE init_monitor_context(
    p_installation_id IN NUMBER,                 -- [CONFIG:monitor:installation]
    p_window_mins     IN NUMBER                  -- [CONFIG:monitor:window]
  )
  IS
    l_fn          CONSTANT VARCHAR2(30) := 'INIT_MONITOR_CONTEXT';
    l_exp_voc     NUMBER;
    l_temp_coeff  NUMBER;
    l_ambient     NUMBER;
    l_inv_count   NUMBER;
    l_warn_pct    NUMBER;
    l_crit_pct    NUMBER;
  BEGIN
    -- [LOG:trace]

    SELECT expected_voc_v, voc_temp_coeff_pct, ambient_temp_c,
           inverter_count, warn_dev_pct, crit_dev_pct
    INTO   l_exp_voc, l_temp_coeff, l_ambient,
           l_inv_count, l_warn_pct, l_crit_pct -- [LOG:debug]
    FROM  (
      SELECT 1 AS installation_id, 164.80 AS expected_voc_v, 0.35 AS voc_temp_coeff_pct,
             28 AS ambient_temp_c, 4 AS inverter_count, 2 AS warn_dev_pct, 8 AS crit_dev_pct FROM DUAL UNION ALL
      SELECT 2,                    231.00,                   0.31,
             39,                   6,                        3,                   10                    FROM DUAL UNION ALL
      SELECT 3,                    160.40,                   0.36,
             31,                   2,                        2,                   8                     FROM DUAL
    )
    WHERE installation_id = p_installation_id;

    v_plant_id               := p_installation_id;   -- [BR:STATE]
    v_window_mins            := p_window_mins;        -- [BR:STATE]
    v_expected_voc           := l_exp_voc;            -- [BR:STATE]
    v_temp_coeff             := l_temp_coeff;         -- [BR:STATE]
    v_ambient_temp           := l_ambient;            -- [BR:STATE]
    v_inverter_count         := l_inv_count;          -- [BR:STATE]
    v_alarm_threshold_pct    := l_warn_pct;           -- [BR:STATE]
    v_critical_threshold_pct := l_crit_pct;           -- [BR:STATE]

    v_alarm_count            := 0;                    -- [BR:STATE]
    v_inverter_faults        := 0;                    -- [BR:STATE]
    v_plant_status           := 'OK';                 -- [BR:STATE]

    -- [LOG:trace]

  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20001,
        c_pkg || '.' || l_fn || ': installation_id ' || p_installation_id || ' not found');
  END init_monitor_context;


  -- ==========================================================================
  -- get_string_deviation -- per-string Voc deviation vs the plant baseline
  -- ==========================================================================
  PROCEDURE get_string_deviation(
    p_string_id    IN  NUMBER,
    p_measured_voc IN  NUMBER,
    p_status       OUT VARCHAR2
  )
  IS
  BEGIN
    -- [LOG:trace]

    v_string_voc := p_measured_voc;                          -- [BR:STATE]

    IF v_expected_voc IS NULL OR v_expected_voc = 0 THEN      -- [BR:LEAF(string:no_baseline)]
      p_status := 'NO_BASELINE';
      RETURN;
    END IF;

    v_deviation_pct := ABS(p_measured_voc - v_expected_voc)
                       / v_expected_voc * 100;                -- [BR:EXPR]

    IF v_deviation_pct <= v_alarm_threshold_pct THEN          -- [BR:LEAF(string:within_tolerance)]
      p_status := 'OK';
    ELSE
      p_status := 'DEVIATED';                                 -- [BR:STATE]
    END IF;

    -- [LOG:trace]
  END get_string_deviation;


  -- ==========================================================================
  -- apply_thermal_derating -- derate factor from cell / ambient temperature
  -- ==========================================================================
  PROCEDURE apply_thermal_derating(
    p_cell_temp_c IN NUMBER
  )
  IS
  BEGIN
    -- [LOG:trace]

    IF v_temp_coeff IS NULL THEN                              -- [BR:LEAF(thermal:no_coeff)]
      v_temp_coeff := 0.35;                                   -- [BR:STATE]
    END IF;

    v_derate_factor := 1 - (GREATEST(p_cell_temp_c - 25, 0)
                       * v_temp_coeff / 100);                 -- [BR:EXPR]

    IF v_ambient_temp > 35 THEN                               -- [BR:LEAF(thermal:hot_ambient)]
      v_derate_factor := v_derate_factor * 0.98;              -- [BR:EXPR]
    END IF;

    IF v_derate_factor < 0.80 THEN                            -- [BR:LEAF(thermal:severe_derate)]
      v_plant_status := 'DERATED';                            -- [BR:STATE]
    END IF;

    -- [LOG:trace]
  END apply_thermal_derating;


  -- ==========================================================================
  -- raise_plant_alarm -- helper: bumps the alarm counter, escalates status
  -- ==========================================================================
  PROCEDURE raise_plant_alarm(
    p_string_id IN NUMBER,
    p_kind      IN VARCHAR2
  )
  IS
  BEGIN
    -- [LOG:trace]

    v_alarm_count := v_alarm_count + 1;                       -- [BR:EXPR]

    IF p_kind = 'CRITICAL' THEN                               -- [BR:LEAF(alarm:critical_kind)]
      v_plant_status := 'CRITICAL';                           -- [BR:STATE]
    END IF;

    DBMS_OUTPUT.PUT_LINE(c_pkg || ': ALARM ' || p_kind
      || ' on string ' || p_string_id || ' (plant ' || v_plant_id || ')');

    -- [LOG:trace]
  END raise_plant_alarm;


  -- ==========================================================================
  -- classify_string_alarm -- routes a string into NONE / WARN / CRITICAL
  -- ==========================================================================
  PROCEDURE classify_string_alarm(
    p_string_id IN NUMBER
  )
  IS
  BEGIN
    -- [LOG:trace]

    IF v_deviation_pct >= v_critical_threshold_pct THEN       -- [BR:FLOW]
      GOTO escalate;                                          -- [BR:FLOW]
    END IF;

    IF v_deviation_pct >= v_alarm_threshold_pct THEN          -- [BR:LEAF(alarm:warn_band)]
      v_alarm_severity := 'WARN';                             -- [BR:STATE]
      raise_plant_alarm(p_string_id, 'WARN');
      RETURN;
    END IF;

    IF v_derate_factor < 0.85 THEN                            -- [BR:LEAF(alarm:thermal_band)]
      v_alarm_severity := 'THERMAL';                          -- [BR:STATE]
      raise_plant_alarm(p_string_id, 'THERMAL');
      RETURN;
    END IF;

    v_alarm_severity := 'NONE';                               -- [BR:STATE]
    RETURN;

    <<escalate>>                                              -- [BR:FLOW]
    v_alarm_severity := 'CRITICAL';                           -- [BR:STATE]
    raise_plant_alarm(p_string_id, 'CRITICAL');

    -- [LOG:trace]
  END classify_string_alarm;


  -- ==========================================================================
  -- check_inverter_status -- counts faulted inverters, escalates plant status
  -- ==========================================================================
  PROCEDURE check_inverter_status
  IS
    l_faults NUMBER;
  BEGIN
    -- [LOG:trace]

    SELECT COUNT(*)
    INTO   l_faults -- [LOG:debug]
    FROM  (
      SELECT 1 AS inv_id, 'FAULT' AS state FROM DUAL UNION ALL
      SELECT 2,           'OK'             FROM DUAL UNION ALL
      SELECT 3,           'OK'             FROM DUAL UNION ALL
      SELECT 4,           'FAULT'          FROM DUAL
    )
    WHERE state = 'FAULT';

    v_inverter_faults := l_faults;                            -- [BR:EXPR]

    IF v_inverter_count IS NOT NULL
       AND l_faults > v_inverter_count / 2 THEN               -- [BR:LEAF(inverter:majority_fault)]
      v_plant_status := 'CRITICAL';                           -- [BR:STATE]
    ELSIF l_faults > 0 THEN                                   -- [BR:LEAF(inverter:partial_fault)]
      v_plant_status := 'DEGRADED';                           -- [BR:STATE]
    END IF;

    -- [LOG:trace]
  END check_inverter_status;


  -- ==========================================================================
  -- calculate_irradiance_index -- DEAD CODE.
  -- Superseded by pkg_solar_irradiance.clear_sky_index. No caller, no callee,
  -- no shared package state. The graph shows it as an orphan.
  -- ==========================================================================
  FUNCTION calculate_irradiance_index(
    p_ghi_w_m2       IN NUMBER,
    p_clear_sky_w_m2 IN NUMBER
  ) RETURN NUMBER
  IS
    l_index NUMBER;
  BEGIN
    -- [LOG:trace]

    IF p_clear_sky_w_m2 = 0 THEN                              -- [BR:LEAF(irradiance:no_clear_sky)]
      RETURN 0;
    END IF;

    l_index := p_ghi_w_m2 / p_clear_sky_w_m2;                 -- [BR:EXPR]

    IF l_index > 1 THEN                                       -- [BR:LEAF(irradiance:overirradiance)]
      l_index := 1;                                           -- [BR:EXPR]
    END IF;

    -- [LOG:trace]
    RETURN ROUND(l_index, 3);
  END calculate_irradiance_index;


  -- ==========================================================================
  -- evaluate_plant_health -- ORCHESTRATOR
  -- ==========================================================================
  FUNCTION evaluate_plant_health(
    p_installation_id IN NUMBER,                 -- [CONFIG:monitor:installation]
    p_window_mins     IN NUMBER DEFAULT 15       -- [CONFIG:monitor:window]
  ) RETURN CLOB
  IS
    l_string_id    NUMBER;
    l_measured_voc NUMBER;
    l_cell_temp    NUMBER;
    l_str_status   VARCHAR2(20);
    l_summary      VARCHAR2(20);
    l_root         JSON_OBJECT_T;
  BEGIN
    -- [LOG:trace]

    init_monitor_context(p_installation_id, p_window_mins);

    FOR rec IN (
      SELECT 1 AS string_id, 163.90 AS measured_voc_v, 47 AS cell_temp_c FROM DUAL UNION ALL
      SELECT 2,              151.20,                    58              FROM DUAL UNION ALL
      SELECT 3,              164.50,                    41              FROM DUAL
    ) LOOP
      l_string_id    := rec.string_id;
      l_measured_voc := rec.measured_voc_v;
      l_cell_temp    := rec.cell_temp_c;

      get_string_deviation(l_string_id, l_measured_voc, l_str_status);
      apply_thermal_derating(l_cell_temp);
      classify_string_alarm(l_string_id);
    END LOOP;

    check_inverter_status;

    -- [BR:EXPR:BEGIN]
    l_root := JSON_OBJECT_T();
    l_root.put('installation_id', v_plant_id);
    l_root.put('window_mins',     v_window_mins);
    l_root.put('alarm_count',     v_alarm_count);
    l_root.put('inverter_faults', v_inverter_faults);
    -- [BR:EXPR:END]

    IF v_plant_status = 'CRITICAL' THEN                       -- [BR:LEAF(plant:critical)]
      l_summary := 'CRITICAL';
    ELSIF v_alarm_count > 0 THEN                              -- [BR:LEAF(plant:has_alarms)]
      l_summary := 'WARN';
    ELSIF v_inverter_faults > 0 THEN                          -- [BR:LEAF(plant:inverter_only)]
      l_summary := 'DEGRADED';
    ELSE
      l_summary := 'OK';
    END IF;

    -- availability / MTBF needs a SCADA history table that does not exist yet
    l_root.put('availability_pct', NULL);                     -- [BR:TODO]

    l_root.put('plant_status', l_summary);

    -- [LOG:trace]
    RETURN l_root.to_clob;

  EXCEPTION
    WHEN OTHERS THEN
      RAISE;
  END evaluate_plant_health;

END pkg_solar_monitor;
/
