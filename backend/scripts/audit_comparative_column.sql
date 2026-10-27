-- Usage: psql "$DATABASE_URL" -v document_id=6 -f backend/scripts/audit_comparative_column.sql
WITH reviews AS (
    SELECT
        period_entry.key AS period_label,
        period_entry.value AS proposal
    FROM documents AS document
    CROSS JOIN LATERAL jsonb_each(
        COALESCE(document.raw_json::jsonb -> 'periods', '{}'::jsonb)
    ) AS period_entry
    WHERE document.id = :'document_id'::integer
),
items AS (
    SELECT
        reviews.period_label,
        statement_entry.key AS statement,
        item.value AS item
    FROM reviews
    CROSS JOIN LATERAL jsonb_each(
        reviews.proposal - 'summary' - 'llm_trace' - 'note_warnings'
    ) AS statement_entry
    CROSS JOIN LATERAL jsonb_array_elements(statement_entry.value) AS item
    WHERE jsonb_typeof(statement_entry.value) = 'array'
)
SELECT
    prior.statement,
    prior.item ->> 'line_item' AS line_item,
    (prior.item ->> 'llm_value')::numeric AS hy2025_llm_value,
    (prior.item ->> 'regex_value')::numeric AS hy2025_regex_value,
    (current.item ->> 'regex_value')::numeric AS hy2026_regex_value
FROM items AS prior
JOIN items AS current
    ON current.statement = prior.statement
    AND current.item ->> 'line_item' = prior.item ->> 'line_item'
WHERE prior.period_label = 'HY2025'
    AND current.period_label = 'HY2026'
    AND prior.item ->> 'llm_value' IS NOT NULL
    AND prior.item ->> 'regex_value' IS NOT NULL
    AND current.item ->> 'regex_value' IS NOT NULL
    AND abs(
        (prior.item ->> 'llm_value')::numeric
        - (current.item ->> 'regex_value')::numeric
    ) <= 1
    AND abs(
        (prior.item ->> 'llm_value')::numeric
        - (prior.item ->> 'regex_value')::numeric
    ) > 1
ORDER BY prior.statement, line_item;
