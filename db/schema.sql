CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    vendor TEXT NOT NULL,
    document_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS template_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(template_id) REFERENCES templates(id)
);

CREATE INDEX IF NOT EXISTS idx_template_versions_template_id
ON template_versions(template_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_template_versions_unique
ON template_versions(template_id, version);

CREATE TABLE IF NOT EXISTS label_options (
    name TEXT PRIMARY KEY
);

DELETE FROM label_options
WHERE rowid NOT IN (
    SELECT MIN(rowid)
    FROM label_options
    GROUP BY lower(name)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_label_options_name_nocase
ON label_options(name COLLATE NOCASE);
