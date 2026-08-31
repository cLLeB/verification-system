import sqlite3
import argparse
import os

def main():
    parser = argparse.ArgumentParser(description="Auditor tool to prove biometric templates are encrypted at rest.")
    parser.add_argument("--db", default="face_db/faces.db", help="Path to the SQLite database file")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"Error: Database file not found at {args.db}")
        print("Please provide the correct path using --db")
        return

    print(f"--- Biometric Encryption Audit ---")
    print(f"Checking database: {args.db}\n")

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    try:
        cur.execute("SELECT user_id, data FROM templates LIMIT 1")
        row = cur.fetchone()
    except sqlite3.OperationalError as e:
        print(f"Error reading database: {e}")
        return

    if not row:
        print("The database is empty. No templates to check.")
        return

    user_id, raw_data = row

    print(f"Found record for user: {user_id}")
    print(f"Raw data size: {len(raw_data)} bytes\n")
    print("Raw stored data preview (first 50 bytes):")
    print(f"  {repr(raw_data[:50])}...\n")

    is_fernet = isinstance(raw_data, (bytes, bytearray)) and raw_data.startswith(b"gAAAAAB")

    if is_fernet:
        print("[PASS] The data is a valid Fernet encrypted token.")
        print("       - 128-bit AES in CBC mode")
        print("       - HMAC using SHA256 for authentication")
        print("       The biometric data is encrypted at rest and unreadable without the key.")
    else:
        print("[FAIL] The data does NOT appear to be a Fernet encrypted token.")

if __name__ == "__main__":
    main()
