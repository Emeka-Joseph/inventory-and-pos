import os
from dotenv import load_dotenv

load_dotenv()

# ── Subscription plan feature gates ──────────────────────────────────────────
PLAN_LIMITS = {
    'trial': {
        'max_products':          None,   # unlimited during trial
        'max_users':             None,
        'has_expense_tracking':  True,
        'has_camera_scanner':    True,
        'has_reorder_alerts':    True,
        'has_full_reports':      True,
        'has_price_history':     True,
        'label':                 'Free Trial',
        'badge_class':           'bg-info text-dark',
    },
    'free': {
        'max_products':          20,
        'max_users':             2,
        'has_expense_tracking':  False,
        'has_camera_scanner':    False,
        'has_reorder_alerts':    False,
        'has_full_reports':      False,
        'has_price_history':     False,
        'label':                 'Free',
        'badge_class':           'bg-secondary text-white',
    },
    'pro': {
        'max_products':          100,
        'max_users':             10,
        'has_expense_tracking':  True,
        'has_camera_scanner':    True,
        'has_reorder_alerts':    True,
        'has_full_reports':      True,
        'has_price_history':     True,
        'label':                 'Pro',
        'badge_class':           'bg-primary text-white',
    },
    'premium': {
        'max_products':          None,
        'max_users':             None,
        'has_expense_tracking':  True,
        'has_camera_scanner':    True,
        'has_reorder_alerts':    True,
        'has_full_reports':      True,
        'has_price_history':     True,
        'label':                 'Premium',
        'badge_class':           'bg-warning text-dark',
    },
}


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL', 'mysql+pymysql://root:@localhost/eventorydb'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True

    # Flask-Mail
    MAIL_SERVER         = 'smtp.gmail.com'
    MAIL_PORT           = 587
    MAIL_USE_TLS        = True
    MAIL_USERNAME       = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD       = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = ('Eventry POS', os.environ.get('MAIL_USERNAME', ''))

    # Super Admin
    SUPERADMIN_USERNAME = os.environ.get('SUPERADMIN_USERNAME', 'superadmin')
    SUPERADMIN_PASSWORD = os.environ.get('SUPERADMIN_PASSWORD', 'changeme')

    # App base URL (used in emails sent from background jobs)
    APP_BASE_URL = os.environ.get('APP_BASE_URL', 'http://localhost:5000')

    # Currencies
    CURRENCIES = [
        ('NGN', '₦',    'Nigerian Naira'),
        ('USD', '$',    'US Dollar'),
        ('GBP', '£',    'British Pound'),
        ('EUR', '€',    'Euro'),
        ('GHS', 'GH₵',  'Ghanaian Cedi'),
        ('KWD', 'د.ك', 'Kuwaiti Dinar'),
        ('KES', 'KSh',  'Kenyan Shilling'),
        ('ZAR', 'R',    'South African Rand'),
        ('AED', 'د.إ',  'UAE Dirham'),
        ('SAR', '﷼',    'Saudi Riyal'),
    ]
