CREATE TABLE episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT,
  status TEXT NOT NULL DEFAULT 'script_pending',
  -- script_pending -> script_ready -> voice_pending -> voice_ready
  -- -> rendering -> review_pending -> approved -> uploaded -> failed
  script_r2_key TEXT,
  voice_r2_key TEXT,
  subtitle_r2_key TEXT,
  render_r2_key TEXT,
  youtube_video_id TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE annotations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER NOT NULL REFERENCES episodes(id),
  timestamp_seconds REAL NOT NULL,
  x REAL,
  y REAL,
  note TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_annotations_episode ON annotations(episode_id);
CREATE INDEX idx_episodes_status ON episodes(status);
