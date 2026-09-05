import os
import sqlite3

DB_NAME = "delivery_tracker.db"
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    import psycopg
    from psycopg.rows import dict_row


class DatabaseConnection:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, params=()):
        return self.conn.execute(query.replace("?", "%s"), params)

    def commit(self):
        return self.conn.commit()

    def close(self):
        return self.conn.close()


def get_db():
    if DATABASE_URL:
        return DatabaseConnection(
            psycopg.connect(DATABASE_URL, row_factory=dict_row)
        )

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def add_column(conn, table, column, definition):
    if DATABASE_URL:
        existing = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
            """,
            (table, column)
        ).fetchone()

        if not existing:
            conn.execute(
                f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}'
            )
    else:
        existing = conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()

        names = [row["name"] for row in existing]

        if column not in names:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )


def init_db():
    print("DATABASE BACKEND:", "POSTGRESQL" if DATABASE_URL else "SQLITE")
    conn = get_db()

    if DATABASE_URL:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shipments (
                id SERIAL PRIMARY KEY,
                tracking_id TEXT UNIQUE NOT NULL,
                sender_name TEXT,
                receiver_name TEXT,
                origin TEXT,
                destination TEXT,
                current_location TEXT,
                status TEXT DEFAULT 'Shipment Created',
                estimated_delivery TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tracking_events (
                id SERIAL PRIMARY KEY,
                shipment_id INTEGER NOT NULL,
                location TEXT,
                status TEXT NOT NULL,
                description TEXT,
                event_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (shipment_id) REFERENCES shipments(id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shipment_photos (
                id SERIAL PRIMARY KEY,
                shipment_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (shipment_id) REFERENCES shipments(id)
            )
            """
        )

    else:
        conn.execute(
            """
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
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tracking_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id INTEGER NOT NULL,
                location TEXT,
                status TEXT NOT NULL,
                description TEXT,
                event_time TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (shipment_id) REFERENCES shipments(id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shipment_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (shipment_id) REFERENCES shipments(id)
            )
            """
        )

    add_column(conn, "shipments", "sender_address", "TEXT")
    add_column(conn, "shipments", "sender_phone", "TEXT")
    add_column(conn, "shipments", "receiver_address", "TEXT")
    add_column(conn, "shipments", "receiver_phone", "TEXT")
    add_column(conn, "shipments", "package_description", "TEXT")
    add_column(conn, "shipments", "package_weight", "TEXT")
    add_column(conn, "shipments", "package_count", "INTEGER DEFAULT 1")
    add_column(conn, "shipments", "receipt_number", "TEXT")
    add_column(conn, "shipments", "receipt_date", "TEXT")
    add_column(conn, "shipments", "sender_country", "TEXT")
    add_column(conn, "shipments", "receiver_country", "TEXT")
    add_column(conn, "shipments", "send_datetime", "TEXT")
    add_column(conn, "shipments", "delivery_datetime", "TEXT")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database ready!")
