"""
Runs the hourly reorder-alert and subscription-reminder checks once, then exits.

Meant to be triggered by a cPanel Cron Job (e.g. hourly), NOT run inside the web
app itself. Running these in-process (the old approach) breaks under LSAPI/
Passenger's multi-process model: every worker process calls create_app()
independently, so an in-process scheduler ends up duplicated once per worker,
all firing the same jobs -- and the same emails -- at the same time. A cron job
is a single process that runs once on schedule regardless of how many web
workers exist, and doesn't take a worker slot away from real traffic to do it.

cPanel cron command (adjust the venv path to match your Setup Python App):
    /home/youruser/virtualenv/yourapp.com/3.9/bin/python /home/youruser/yourapp.com/run_scheduled_tasks.py
Usage: python run_scheduled_tasks.py
"""
from app import create_app
from app.utils import check_reorder_alerts, check_subscriptions

app = create_app()
check_reorder_alerts(app)
check_subscriptions(app)
