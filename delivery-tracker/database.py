import sqlite3

DB_NAME = "delivery_tracker.db"


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def add_column(conn, table, column, definition):
    existing = conn.execute(f"PRAGMA table_info({table})").fetchall()
    names = [row["name"] for row in existing]

    if column not in names:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS shipments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_id TEXT UNIQUE NOT NULL,
            sender_name TEXT,
            receiver_name TEXT,
            origin TEXT,
            destination TEXT,
            current_location TEXT,
            status TEXT DEFAULT 'Shipment Created',
            estimated_delivery TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracking_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shipment_id INTEGER NOT NULL,
            location TEXT,
            status TEXT NOT NULL,
            description TEXT,
            event_time TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shipment_id) REFERENCES shipments(id)
        )
    """)

    # Additional shipment information
    add_column(conn, "shipments", "sender_address", "TEXT")
    add_column(conn, "shipments", "sender_phone", "TEXT")
    add_column(conn, "shipments", "receiver_address", "TEXT")
    add_column(conn, "shipments", "receiver_phone", "TEXT")
    add_column(conn, "shipments", "package_description", "TEXT")
    add_column(conn, "shipments", "package_weight", "TEXT")
    add_column(conn, "shipments", "package_count", "INTEGER DEFAULT 1")

    # Receipt information
    add_column(conn, "shipments", "receipt_number", "TEXT")
    add_column(conn, "shipments", "receipt_date", "TEXT")
    add_column(conn, "shipments", "sender_country", "TEXT")
    add_column(conn, "shipments", "receiver_country", "TEXT")
    add_column(conn, "shipments", "send_datetime", "TEXT")
    add_column(conn, "shipments", "delivery_datetime", "TEXT")

    # Shipment photos
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shipment_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shipment_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shipment_id) REFERENCES shipments(id)
        )
    """)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database ready!")
