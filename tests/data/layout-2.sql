-- A state file of layout 2, as SQLite's dump: one pass of the tests' split kind over unit
-- "a" with FakeGateway, by a liulab-llms that wrote layout 2, then sqlite3's iterdump, with
-- the application_id and user_version pragmas written above it. Layout 2 never changes, so
-- neither does this file.
PRAGMA application_id=1819045236;
PRAGMA user_version=2;
BEGIN TRANSACTION;
CREATE TABLE checks (
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        pass_id INTEGER NOT NULL,
        seq INTEGER NOT NULL,
        name TEXT NOT NULL,
        message TEXT,
        PRIMARY KEY (kind, key, pass_id, seq),
        FOREIGN KEY (kind, key, pass_id) REFERENCES outcomes
    );
CREATE TABLE outcomes (
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        pass_id INTEGER NOT NULL REFERENCES passes,
        ending TEXT NOT NULL,
        inputs_tag TEXT,
        answer TEXT,
        reason TEXT, spent_usd REAL, first_cost_usd REAL, requests INTEGER, cache_hits INTEGER, usage TEXT, seconds REAL,
        PRIMARY KEY (kind, key, pass_id)
    );
INSERT INTO "outcomes" VALUES('split','a',1,'settled','text=red green','{"words": ["red", "green"]}',NULL,0.027,0.027,1,0,'{"input_tokens": 9, "output_tokens": 27}',0.0);
CREATE TABLE passes (
        pass_id INTEGER PRIMARY KEY,
        meter TEXT NOT NULL,
        kind TEXT NOT NULL,
        kind_version TEXT NOT NULL,
        label TEXT NOT NULL,
        models TEXT NOT NULL,
        ceiling_usd REAL NOT NULL,
        off_peak_only INTEGER NOT NULL,
        repo_version TEXT NOT NULL,
        labllm_version TEXT NOT NULL,
        stopped TEXT,
        spent_usd REAL,
        first_cost_usd REAL,
        requests INTEGER,
        cache_hits INTEGER
    );
INSERT INTO "passes" VALUES(1,'a37468d88df749289fcb2e5d237a8539','split','1','split','{"writer": {"provider": "openai", "name": "test-writer", "thinking": null, "effort": null, "temperature": null, "max_output_tokens": 50}}',1.0,0,'unknown','2026.11.1.dev25+g4c379cb34',NULL,0.027,0.027,1,0);
COMMIT;
