"""
Run this script ONCE to create all database tables.
Usage: python setup_db.py
"""
import pymysql
from dotenv import load_dotenv
import os

load_dotenv()

DB_URL = os.environ.get('DATABASE_URL', 'mysql+pymysql://root:@localhost/eventry_pos_db')

# Extract credentials from URL
# Format: mysql+pymysql://user:pass@host/dbname
try:
    url = DB_URL.replace('mysql+pymysql://', '')
    userpass, hostdb = url.split('@')
    if ':' in userpass:
        db_user, db_pass = userpass.split(':', 1)
    else:
        db_user, db_pass = userpass, ''
    host, db_name = hostdb.split('/', 1)
except Exception:
    print("Could not parse DATABASE_URL. Edit this file manually.")
    raise

print(f"Connecting to MySQL at {host} as '{db_user}'...")
conn = pymysql.connect(host=host, user=db_user, password=db_pass)
cursor = conn.cursor()
cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
conn.commit()
cursor.close()
conn.close()
print(f"Database '{db_name}' ready.")

from app import create_app
from app.extensions import db

app = create_app()
with app.app_context():
    db.create_all()
    print("All tables created successfully.")
    print("\nDone! Visit http://localhost:5000/ to register your first business.")
