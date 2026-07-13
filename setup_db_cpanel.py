"""
Run this ONCE on cPanel to create all database tables.
Unlike setup_db.py, this does NOT attempt to CREATE DATABASE — shared cPanel
MySQL users normally can't do that. Create the database and its user via
cPanel's "MySQL Databases" tool first, point DATABASE_URL at it in .env,
then run: python setup_db_cpanel.py
"""
from app import create_app
from app.extensions import db

app = create_app()
with app.app_context():
    db.create_all()
    print("All tables created successfully.")
    print("\nDone! Visit your site's /register page to create the first business.")
