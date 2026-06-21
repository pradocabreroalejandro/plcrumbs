CREATE OR REPLACE PACKAGE pkg_solar_energy AS
/*
  Solar installation panel-string properties.
  A "string" is a set of panels wired in series sharing the same Voc and Isc.
*/

  FUNCTION get_panel_strings(
    p_installation_id IN NUMBER
  ) RETURN CLOB;

  FUNCTION get_string_health(
    p_installation_id IN NUMBER,
    p_string_id       IN NUMBER
  ) RETURN VARCHAR2;

END pkg_solar_energy;
/
