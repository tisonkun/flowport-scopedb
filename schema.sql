CREATE TABLE IF NOT EXISTS scopedb.public.flowport_demo_points (
    measurement string,
    tags object,
    fields object,
    `timestamp` int,
    category string
);

CREATE OR REPLACE VIEW scopedb.public.flowport_demo_logs AS
FROM scopedb.public.flowport_demo_points
WHERE category = 'logging'
SELECT
    `timestamp`::timestamp AS time,
    tags['service']::string AS service,
    tags['env']::string AS env,
    tags['demo_run']::string AS demo_run,
    fields['event_id']::string AS event_id,
    fields['message']::string AS message,
    fields['duration_ms']::int AS duration_ms;
