-- Seed ClickHouse with sample LLM trace data for UI testing

CREATE TABLE IF NOT EXISTS default.llogr_events
(
    session_id      String,
    timestamp       DateTime,
    model           String,
    prompt_tokens   UInt32,
    completion_tokens UInt32,
    cost            Float64,
    message         String,
    is_cached       UInt8
)
ENGINE = MergeTree()
ORDER BY (timestamp, session_id);

INSERT INTO default.llogr_events VALUES
    ('sess-001', '2026-05-31 10:00:00', 'gpt-4o',        512,  128, 0.0142, 'Summarise the document.',            0),
    ('sess-001', '2026-05-31 10:00:05', 'gpt-4o',        128,   64, 0.0035, 'The document covers AI safety.',     1),
    ('sess-002', '2026-05-31 10:05:00', 'gpt-4o-mini',   256,   80, 0.0012, 'Translate to French.',               0),
    ('sess-002', '2026-05-31 10:05:03', 'gpt-4o-mini',    80,   96, 0.0014, 'Voici la traduction…',               0),
    ('sess-003', '2026-05-31 11:00:00', 'claude-sonnet',  400,  200, 0.0180, 'Write a unit test for this code.',   0),
    ('sess-003', '2026-05-31 11:00:08', 'claude-sonnet',  200,  350, 0.0245, 'Here is a pytest suite…',            0),
    ('sess-004', '2026-05-31 12:00:00', 'gpt-4o',        800,  300, 0.0330, 'Explain transformers to a child.',   0),
    ('sess-004', '2026-05-31 12:00:12', 'gpt-4o',        300,  420, 0.0462, 'Imagine tokens are Lego bricks…',   1),
    ('sess-005', '2026-05-31 13:00:00', 'gpt-4o-mini',   100,   40, 0.0006, 'What is 2+2?',                       1),
    ('sess-005', '2026-05-31 13:00:01', 'gpt-4o-mini',    40,   10, 0.0002, '4',                                  1),
    ('sess-006', '2026-05-31 14:00:00', 'claude-opus',  1200,  600, 0.1200, 'Deep dive: RAG architecture.',       0),
    ('sess-006', '2026-05-31 14:01:00', 'claude-opus',   600,  900, 0.1800, 'RAG combines retrieval with…',       0),
    ('sess-007', '2026-05-31 15:00:00', 'gpt-4o',        350,  150, 0.0165, 'Review this PR description.',        0),
    ('sess-007', '2026-05-31 15:00:10', 'gpt-4o',        150,  200, 0.0220, 'The PR looks good overall…',         0),
    ('sess-008', '2026-05-31 16:00:00', 'gpt-4o-mini',   500,  250, 0.0037, 'Extract JSON from this text.',       0),
    ('sess-008', '2026-05-31 16:00:04', 'gpt-4o-mini',   250,  180, 0.0027, '{"name":"Alice","age":30}',          0),
    ('sess-009', '2026-05-31 17:00:00', 'claude-sonnet', 700,  400, 0.0360, 'Generate SQL for this schema.',      0),
    ('sess-009', '2026-05-31 17:00:15', 'claude-sonnet', 400,  550, 0.0495, 'SELECT * FROM users WHERE…',         0),
    ('sess-010', '2026-05-31 18:00:00', 'gpt-4o',        200,   80, 0.0088, 'Is this sentence grammatical?',      1),
    ('sess-010', '2026-05-31 18:00:02', 'gpt-4o',         80,   20, 0.0022, 'Yes, the sentence is correct.',      1);

-- Media events table — used for asset resolution UI tests (media_url/thumbnail_url → MinIO)
CREATE TABLE IF NOT EXISTS default.media_events
(
    id             String,
    recorded_at    DateTime,
    title          String,
    media_url      String,
    thumbnail_url  String
)
ENGINE = MergeTree()
ORDER BY (recorded_at, id);

INSERT INTO default.media_events VALUES
    ('item-001', '2026-06-01 10:00:00', 'Product demo', 'http://localhost:9200/test-media/clip.mp4',  'http://localhost:9200/test-media/photo.jpg'),
    ('item-002', '2026-06-01 11:00:00', 'Tutorial',     'http://localhost:9200/test-media/clip.mp4',  'http://localhost:9200/test-media/photo.jpg'),
    ('item-003', '2026-06-01 12:00:00', 'Interview',    'http://localhost:9200/test-media/audio.mp3', 'http://localhost:9200/test-media/photo.jpg');
