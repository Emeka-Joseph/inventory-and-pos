from datetime import datetime, date, timedelta
from decimal import Decimal
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db


class Business(db.Model):
    __tablename__ = 'businesses'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    phone = db.Column(db.String(30))
    address = db.Column(db.Text)
    currency = db.Column(db.String(10), default='NGN')
    currency_symbol = db.Column(db.String(5), default='₦')
    status    = db.Column(db.Enum('pending', 'active', 'suspended'), nullable=False, default='pending')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship('User', backref='business', lazy=True, cascade='all, delete-orphan')
    categories = db.relationship('Category', backref='business', lazy=True, cascade='all, delete-orphan')
    products = db.relationship('Product', backref='business', lazy=True, cascade='all, delete-orphan')
    sales = db.relationship('Sale', backref='business', lazy=True, cascade='all, delete-orphan')
    stock_entries = db.relationship('StockEntry', backref='business', lazy=True, cascade='all, delete-orphan')
    expenses      = db.relationship('Expense', backref='business', lazy=True, cascade='all, delete-orphan')
    subscription  = db.relationship('Subscription', uselist=False, backref='business',
                                    cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Business {self.name}>'


class User(db.Model, UserMixin):
    __tablename__ = 'users'
    __table_args__ = (
        db.UniqueConstraint('business_id', 'username', name='uq_business_username'),
    )

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('businesses.id'), nullable=False)
    username = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(200))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.Enum('admin', 'store_keeper', 'sales_rep'), nullable=False, default='sales_rep')
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sales = db.relationship('Sale', backref='seller', lazy=True, foreign_keys='Sale.user_id')
    stock_entries = db.relationship('StockEntry', backref='recorder', lazy=True, foreign_keys='StockEntry.user_id')
    work_sessions = db.relationship('WorkSession', backref='user', lazy=True, foreign_keys='WorkSession.user_id')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def full_name(self):
        parts = filter(None, [self.first_name, self.last_name])
        return ' '.join(parts) or self.username

    @property
    def is_admin(self):
        return self.role == 'admin'

    @property
    def is_store_keeper(self):
        return self.role in ('admin', 'store_keeper')

    @property
    def is_sales_rep(self):
        return self.role in ('admin', 'store_keeper', 'sales_rep')

    def active_session(self):
        return WorkSession.query.filter_by(user_id=self.id, is_active=True).first()

    def __repr__(self):
        return f'<User {self.username} ({self.role})>'


class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('businesses.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    products = db.relationship('Product', backref='category', lazy=True)

    def __repr__(self):
        return f'<Category {self.name}>'


class Product(db.Model):
    __tablename__ = 'products'
    __table_args__ = (
        db.UniqueConstraint('business_id', 'barcode', name='uq_business_barcode'),
    )

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('businesses.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    name = db.Column(db.String(200), nullable=False)
    barcode = db.Column(db.String(100), nullable=False)
    manufacture_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    description = db.Column(db.Text)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    cost_price = db.Column(db.Numeric(12, 2), default=0)
    quantity_in_stock = db.Column(db.Integer, default=0)
    reorder_level = db.Column(db.Integer, default=5)
    unit = db.Column(db.String(50), default='piece')
    is_active             = db.Column(db.Boolean, default=True)
    reorder_alert_sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    stock_entries = db.relationship('StockEntry', backref='product', lazy=True)
    sale_items = db.relationship('SaleItem', backref='product', lazy=True)
    price_history = db.relationship('PriceHistory', backref='product', lazy=True)

    @property
    def is_low_stock(self):
        return self.quantity_in_stock <= self.reorder_level

    @property
    def expiry_status(self):
        if not self.expiry_date:
            return 'none'
        today = date.today()
        if self.expiry_date < today:
            return 'expired'
        if self.expiry_date <= today + timedelta(days=30):
            return 'expiring_soon'
        return 'ok'

    @property
    def stock_value(self):
        return Decimal(str(self.cost_price or 0)) * self.quantity_in_stock

    def __repr__(self):
        return f'<Product {self.name}>'


class StockEntry(db.Model):
    """Immutable record of stock received. Cannot be altered after creation."""
    __tablename__ = 'stock_entries'

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('businesses.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.Numeric(12, 2), default=0)
    total_cost = db.Column(db.Numeric(12, 2), default=0)
    reference = db.Column(db.String(100))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<StockEntry product_id={self.product_id} qty={self.quantity}>'


class WorkSession(db.Model):
    """Tracks sales rep clock-in / clock-out."""
    __tablename__ = 'work_sessions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey('businesses.id'), nullable=False)
    clock_in = db.Column(db.DateTime, default=datetime.utcnow)
    clock_out = db.Column(db.DateTime, nullable=True)
    work_date = db.Column(db.Date, default=date.today)
    is_active = db.Column(db.Boolean, default=True)

    sales = db.relationship('Sale', backref='work_session', lazy=True)

    @property
    def duration_str(self):
        if not self.clock_out:
            return 'Active'
        delta = self.clock_out - self.clock_in
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes = remainder // 60
        return f'{hours}h {minutes}m'

    def __repr__(self):
        return f'<WorkSession user_id={self.user_id} date={self.work_date}>'


class Sale(db.Model):
    __tablename__ = 'sales'

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('businesses.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('work_sessions.id'), nullable=True)
    sale_number = db.Column(db.String(60), unique=True, nullable=False)
    subtotal = db.Column(db.Numeric(12, 2), default=0)
    discount = db.Column(db.Numeric(12, 2), default=0)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False)
    amount_tendered = db.Column(db.Numeric(12, 2), default=0)
    change_given = db.Column(db.Numeric(12, 2), default=0)
    payment_method = db.Column(db.Enum('cash', 'card', 'transfer', 'other'), default='cash')
    customer_name = db.Column(db.String(200))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship('SaleItem', backref='sale', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Sale {self.sale_number}>'


class SaleItem(db.Model):
    __tablename__ = 'sale_items'

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    product_name = db.Column(db.String(200))
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False)

    def __repr__(self):
        return f'<SaleItem {self.product_name} x{self.quantity}>'


class PriceHistory(db.Model):
    __tablename__ = 'price_history'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    old_price = db.Column(db.Numeric(12, 2))
    new_price = db.Column(db.Numeric(12, 2))
    changed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)

    changer = db.relationship('User', foreign_keys=[changed_by])

    def __repr__(self):
        return f'<PriceHistory product_id={self.product_id}>'


class OtpVerification(db.Model):
    """Short-lived OTP for email verification during business registration."""
    __tablename__ = 'otp_verifications'

    id         = db.Column(db.Integer, primary_key=True)
    email      = db.Column(db.String(200), nullable=False, index=True)
    otp        = db.Column(db.String(10), nullable=False)
    verified   = db.Column(db.Boolean, default=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_expired(self):
        return datetime.utcnow() > self.expires_at

    def __repr__(self):
        return f'<OtpVerification {self.email}>'


class Expense(db.Model):
    __tablename__ = 'expenses'

    CATEGORIES = ['Rent', 'Salaries', 'Utilities', 'Taxes', 'Transport',
                  'Maintenance', 'Marketing', 'Insurance', 'Other']

    id          = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey('businesses.id'), nullable=False)
    category    = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(200))
    amount      = db.Column(db.Numeric(12, 2), nullable=False)
    expense_date = db.Column(db.Date, nullable=False, default=date.today)
    recorded_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    recorder = db.relationship('User', foreign_keys=[recorded_by])

    def __repr__(self):
        return f'<Expense {self.category} {self.amount}>'


class Subscription(db.Model):
    __tablename__ = 'subscriptions'

    id            = db.Column(db.Integer, primary_key=True)
    business_id   = db.Column(db.Integer, db.ForeignKey('businesses.id'),
                               nullable=False, unique=True)
    plan          = db.Column(db.Enum('free', 'pro', 'premium'),
                               nullable=False, default='free')
    billing_cycle = db.Column(db.Enum('monthly', 'annual'), nullable=True)
    status        = db.Column(db.Enum('trialing', 'active', 'expired', 'cancelled'),
                               nullable=False, default='trialing')
    trial_ends_at = db.Column(db.DateTime, nullable=True)
    period_start  = db.Column(db.DateTime, nullable=True)
    period_end    = db.Column(db.DateTime, nullable=True)

    # Stripe placeholders (wired in next session)
    stripe_customer_id     = db.Column(db.String(120), nullable=True)
    stripe_subscription_id = db.Column(db.String(120), nullable=True)

    # Trial reminder flags
    trial_r5d_sent = db.Column(db.Boolean, default=False)
    trial_r2d_sent = db.Column(db.Boolean, default=False)
    trial_r1d_sent = db.Column(db.Boolean, default=False)
    # Paid-plan expiry reminder flags
    plan_r5d_sent  = db.Column(db.Boolean, default=False)
    plan_r2d_sent  = db.Column(db.Boolean, default=False)
    plan_r1d_sent  = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── Computed properties ───────────────────────────────────────────────────

    @property
    def effective_plan_key(self):
        """Returns 'trial', 'pro', 'premium', or 'free'."""
        if (self.status == 'trialing' and self.trial_ends_at and
                datetime.utcnow() <= self.trial_ends_at):
            return 'trial'
        if self.status == 'active' and self.plan in ('pro', 'premium'):
            if self.period_end is None or datetime.utcnow() <= self.period_end:
                return self.plan
        return 'free'

    @property
    def features(self):
        from .config import PLAN_LIMITS
        return PLAN_LIMITS[self.effective_plan_key]

    @property
    def trial_days_left(self):
        if not self.trial_ends_at:
            return 0
        secs = (self.trial_ends_at - datetime.utcnow()).total_seconds()
        return max(0, int(secs / 86400))

    @property
    def days_until_expiry(self):
        if self.status == 'trialing':
            end = self.trial_ends_at
        elif self.status == 'active':
            end = self.period_end
        else:
            return None
        if not end:
            return None
        secs = (end - datetime.utcnow()).total_seconds()
        return max(0, int(secs / 86400))

    @property
    def is_on_paid_plan(self):
        return self.effective_plan_key in ('pro', 'premium')

    @property
    def display_status(self):
        mapping = {
            'trial':   'Free Trial',
            'free':    'Free',
            'pro':     'Pro',
            'premium': 'Premium',
        }
        return mapping.get(self.effective_plan_key, 'Free')

    def __repr__(self):
        return f'<Subscription biz={self.business_id} {self.plan}/{self.status}>'
