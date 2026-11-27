-- Disposable local acceptance role. Do not apply to deployed Neon.
CREATE ROLE ei_cross_audit NOLOGIN;
GRANT USAGE ON SCHEMA ei2 TO ei_cross_audit;
GRANT SELECT ON ei2.state,ei2.grants,ei2.events TO ei_cross_audit;
