-- Usage: psql "$DATABASE_URL" -v document_id=6 -f backend/scripts/audit_extraction_coverage.sql
WITH reviews AS (
    SELECT
        document.id AS document_id,
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
        reviews.document_id,
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
    period_label,
    statement,
    item ->> 'status' AS status,
    count(*) AS line_count,
    string_agg(item ->> 'line_item', ', ' ORDER BY item ->> 'line_item') AS line_items
FROM items
GROUP BY period_label, statement, item ->> 'status'
ORDER BY period_label, statement, status;
