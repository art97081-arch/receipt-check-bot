import sqlite3
from typing import List, Optional

DB_PATH = 'bot_data.db'


def init_db(owner_tg_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS allowed_users (
        tg_id INTEGER PRIMARY KEY
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS owners (
        tg_id INTEGER PRIMARY KEY
    )
    ''')

    # Keep legacy meta value for backward compatibility.
    cur.execute('REPLACE INTO meta (key, value) VALUES (?, ?)', ('owner_tg_id', str(owner_tg_id)))

    # Seed owners from env and legacy meta if present.
    cur.execute('INSERT OR IGNORE INTO owners (tg_id) VALUES (?)', (owner_tg_id,))
    cur.execute('SELECT value FROM meta WHERE key = ?', ('owner_tg_id',))
    row = cur.fetchone()
    if row and str(row[0]).strip().isdigit():
        cur.execute('INSERT OR IGNORE INTO owners (tg_id) VALUES (?)', (int(row[0]),))

    conn.commit()
    conn.close()


def get_owner() -> Optional[int]:
    owners = list_owners()
    return owners[0] if owners else None


def list_owners() -> List[int]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT tg_id FROM owners ORDER BY tg_id')
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]


def is_owner(tg_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT 1 FROM owners WHERE tg_id = ?', (tg_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row)


def add_owner(tg_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT OR IGNORE INTO owners (tg_id) VALUES (?)', (tg_id,))
    conn.commit()
    conn.close()


def remove_owner(tg_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM owners')
    count = int(cur.fetchone()[0] or 0)
    if count <= 1:
        conn.close()
        return False
    cur.execute('DELETE FROM owners WHERE tg_id = ?', (tg_id,))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def add_allowed(tg_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT OR IGNORE INTO allowed_users (tg_id) VALUES (?)', (tg_id,))
    conn.commit()
    conn.close()


def remove_allowed(tg_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('DELETE FROM allowed_users WHERE tg_id = ?', (tg_id,))
    conn.commit()
    conn.close()


def list_allowed() -> List[int]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT tg_id FROM allowed_users ORDER BY tg_id')
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]


def is_allowed(tg_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT 1 FROM allowed_users WHERE tg_id = ?', (tg_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row)
