from functools import wraps
from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, session, current_app, abort)
from datetime import datetime, timedelta
from ..extensions import db
from ..models import Business, User, Subscription

superadmin_bp = Blueprint('superadmin', __name__)


# ── Auth helper ───────────────────────────────────────────────────────────────

def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('superadmin_logged_in'):
            return redirect(url_for('superadmin.login'))
        return f(*args, **kwargs)
    return decorated


# ── Login / Logout ────────────────────────────────────────────────────────────

@superadmin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('superadmin_logged_in'):
        return redirect(url_for('superadmin.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if (username == current_app.config['SUPERADMIN_USERNAME'] and
                password == current_app.config['SUPERADMIN_PASSWORD']):
            session['superadmin_logged_in'] = True
            session['superadmin_username'] = username
            flash('Welcome, Super Admin.', 'success')
            return redirect(url_for('superadmin.dashboard'))
        flash('Invalid credentials.', 'danger')

    return render_template('superadmin/login.html')


@superadmin_bp.route('/logout')
def logout():
    session.pop('superadmin_logged_in', None)
    session.pop('superadmin_username', None)
    flash('Logged out.', 'info')
    return redirect(url_for('superadmin.login'))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@superadmin_bp.route('/')
@superadmin_required
def dashboard():
    businesses = Business.query.order_by(Business.created_at.desc()).all()

    stats = {
        'total':     len(businesses),
        'active':    sum(1 for b in businesses if b.status == 'active'),
        'pending':   sum(1 for b in businesses if b.status == 'pending'),
        'suspended': sum(1 for b in businesses if b.status == 'suspended'),
    }

    for biz in businesses:
        biz._user_count = User.query.filter_by(business_id=biz.id).count()

    return render_template('superadmin/dashboard.html',
                           businesses=businesses, stats=stats,
                           now=datetime.utcnow())


# ── Business status actions ───────────────────────────────────────────────────

@superadmin_bp.route('/businesses/<int:biz_id>/approve', methods=['POST'])
@superadmin_required
def approve(biz_id):
    biz = Business.query.get_or_404(biz_id)
    biz.status = 'active'

    # Create subscription (trialing) if not already present
    if not biz.subscription:
        sub = Subscription(
            business_id=biz.id,
            plan='free',
            status='trialing',
            trial_ends_at=datetime.utcnow() + timedelta(days=14),
        )
        db.session.add(sub)

    db.session.commit()
    try:
        from ..utils import send_approval_email
        send_approval_email(biz)
    except Exception:
        pass
    flash(f'"{biz.name}" approved — 14-day trial started.', 'success')
    return redirect(url_for('superadmin.dashboard'))


@superadmin_bp.route('/businesses/<int:biz_id>/set-plan', methods=['POST'])
@superadmin_required
def set_plan(biz_id):
    biz = Business.query.get_or_404(biz_id)
    plan   = request.form.get('plan', 'free')
    cycle  = request.form.get('billing_cycle', 'monthly')
    status = request.form.get('status', 'active')

    if plan not in ('free', 'pro', 'premium'):
        flash('Invalid plan.', 'danger')
        return redirect(url_for('superadmin.dashboard'))

    sub = biz.subscription
    if not sub:
        sub = Subscription(business_id=biz.id)
        db.session.add(sub)

    sub.plan   = plan
    sub.status = status
    if status == 'active' and plan in ('pro', 'premium'):
        sub.billing_cycle = cycle
        sub.period_start  = datetime.utcnow()
        if cycle == 'annual':
            sub.period_end = datetime.utcnow() + timedelta(days=365)
        else:
            sub.period_end = datetime.utcnow() + timedelta(days=30)
        # reset reminder flags when plan is manually activated
        sub.plan_r5d_sent = sub.plan_r2d_sent = sub.plan_r1d_sent = False

    db.session.commit()
    flash(f'"{biz.name}" plan set to {plan} ({status}).', 'success')
    return redirect(url_for('superadmin.dashboard'))


@superadmin_bp.route('/businesses/<int:biz_id>/suspend', methods=['POST'])
@superadmin_required
def suspend(biz_id):
    biz = Business.query.get_or_404(biz_id)
    biz.status = 'suspended'
    db.session.commit()
    flash(f'"{biz.name}" has been suspended.', 'warning')
    return redirect(url_for('superadmin.dashboard'))


@superadmin_bp.route('/businesses/<int:biz_id>/activate', methods=['POST'])
@superadmin_required
def activate(biz_id):
    biz = Business.query.get_or_404(biz_id)
    biz.status = 'active'
    db.session.commit()
    flash(f'"{biz.name}" has been re-activated.', 'success')
    return redirect(url_for('superadmin.dashboard'))


@superadmin_bp.route('/businesses/<int:biz_id>/delete', methods=['POST'])
@superadmin_required
def delete_business(biz_id):
    biz = Business.query.get_or_404(biz_id)
    name = biz.name
    db.session.delete(biz)
    db.session.commit()
    flash(f'Business "{name}" permanently deleted.', 'danger')
    return redirect(url_for('superadmin.dashboard'))
