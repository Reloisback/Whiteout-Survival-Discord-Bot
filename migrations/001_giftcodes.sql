CREATE TABLE IF NOT EXISTS gift_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'unused'
);

CREATE TABLE IF NOT EXISTS user_gift_codes (
    user_id INTEGER,
    gift_code_id INTEGER,
    used_at TIMESTAMP,
    PRIMARY KEY (user_id, gift_code_id),
    FOREIGN KEY (user_id) REFERENCES users(fid),
    FOREIGN KEY (gift_code_id) REFERENCES gift_codes(id)
);
