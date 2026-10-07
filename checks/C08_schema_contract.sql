-- id: C08
-- severity: error
-- gate: yes
-- description: staged schema differs from contract (column missing, unexpected, or of another type)
--
-- The contract is written here, not read from the transform or from config
-- (D28): a control must not share a source with what it controls, so a
-- schema change has to be made twice, once in transform/ and once here, to
-- pass. It mirrors design.md section 3. Column order and nullability are not
-- part of it: staging unions by name, and NULL meaning is per column (D25).
--
-- Compared both ways against information_schema.columns of the two staged
-- tables the runner binds, so a dropped column, an added column and a changed
-- type each return a row.

WITH contract (table_name, column_name, data_type) AS (
    VALUES
        ('observations',   'series_id',         'VARCHAR'),
        ('observations',   'obs_date',          'DATE'),
        ('observations',   'value',             'DOUBLE'),
        ('observations',   'units',             'VARCHAR'),
        ('observations',   'available_date',    'DATE'),
        ('observations',   'vintage',           'DATE'),
        ('observations',   'run_id',            'VARCHAR'),
        ('observations',   'loaded_at',         'TIMESTAMP WITH TIME ZONE'),
        ('series_catalog', 'series_id',         'VARCHAR'),
        ('series_catalog', 'name',              'VARCHAR'),
        ('series_catalog', 'source',            'VARCHAR'),
        ('series_catalog', 'source_key',        'VARCHAR'),
        ('series_catalog', 'entity_type',       'VARCHAR'),
        ('series_catalog', 'entity_id',         'VARCHAR'),
        ('series_catalog', 'measure',           'VARCHAR'),
        ('series_catalog', 'units',             'VARCHAR'),
        ('series_catalog', 'frequency',         'VARCHAR'),
        ('series_catalog', 'adjustment',        'VARCHAR'),
        ('series_catalog', 'kind',              'VARCHAR'),
        ('series_catalog', 'active_from',       'DATE'),
        ('series_catalog', 'active_to',         'DATE'),
        ('series_catalog', 'expected_lag_days', 'INTEGER')
),
staged AS (
    SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_name IN ('observations', 'series_catalog')
)
SELECT
    coalesce(c.table_name, s.table_name)   AS table_name,
    coalesce(c.column_name, s.column_name) AS column_name,
    c.data_type                            AS contract_type,
    s.data_type                            AS staged_type,
    CASE WHEN s.column_name IS NULL THEN 'missing'
         WHEN c.column_name IS NULL THEN 'unexpected'
         ELSE 'type' END                   AS problem
FROM contract AS c
FULL OUTER JOIN staged AS s
    ON c.table_name = s.table_name AND c.column_name = s.column_name
WHERE c.column_name IS NULL
   OR s.column_name IS NULL
   OR c.data_type <> s.data_type
ORDER BY table_name, column_name
