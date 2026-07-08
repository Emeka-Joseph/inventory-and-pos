import random
import re
from datetime import datetime, timedelta
from functools import wraps
from flask import abort, g, redirect, url_for, flash, current_app
from flask_login import current_user
from flask_mail import Message
from .extensions import db, mail


# ── Slug ─────────────────────────────────────────────────────────────────────

def slugify(text):
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    text = re.sub(r'^-+|-+$', '', text)
    return text


# ── Sale helpers ─────────────────────────────────────────────────────────────

def generate_sale_number(business):
    from .models import Sale
    today = datetime.utcnow()
    prefix = f"{business.slug[:6].upper().replace('-', '')}-{today.strftime('%Y%m%d')}"
    count = Sale.query.filter(
        Sale.business_id == business.id,
        Sale.sale_number.like(f'{prefix}%')
    ).count()
    return f"{prefix}-{str(count + 1).zfill(4)}"


# ── Business loader ───────────────────────────────────────────────────────────

def load_business_or_404(slug):
    from .models import Business
    biz = Business.query.filter_by(slug=slug).first_or_404()
    if biz.status != 'active':
        abort(403)
    return biz


# ── Access decorators ─────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            abort(403)
        if hasattr(g, 'business') and current_user.business_id != g.business.id:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def store_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ('admin', 'store_keeper'):
            abort(403)
        if hasattr(g, 'business') and current_user.business_id != g.business.id:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def pos_access_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            slug = kwargs.get('slug', '')
            return redirect(url_for('pos.pos_login', slug=slug))
        if hasattr(g, 'business') and current_user.business_id != g.business.id:
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ── Date ranges ───────────────────────────────────────────────────────────────

def get_date_range(period):
    today = datetime.utcnow().date()
    if period == 'today':
        start = datetime.combine(today, datetime.min.time())
        end   = datetime.combine(today, datetime.max.time())
    elif period == 'week':
        start = datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time())
        end   = datetime.combine(today, datetime.max.time())
    elif period == 'month':
        start = datetime.combine(today.replace(day=1), datetime.min.time())
        end   = datetime.combine(today, datetime.max.time())
    elif period == 'year':
        start = datetime.combine(today.replace(month=1, day=1), datetime.min.time())
        end   = datetime.combine(today, datetime.max.time())
    else:
        start = datetime.combine(today, datetime.min.time())
        end   = datetime.combine(today, datetime.max.time())
    return start, end


# ── Sales summary ─────────────────────────────────────────────────────────────

def sales_summary(business_id, start, end):
    from .models import Sale
    from sqlalchemy import func
    sales = Sale.query.filter(
        Sale.business_id == business_id,
        Sale.created_at >= start,
        Sale.created_at <= end
    )
    total_revenue  = sales.with_entities(func.sum(Sale.total_amount)).scalar() or 0
    total_discount = sales.with_entities(func.sum(Sale.discount)).scalar() or 0
    total_count    = sales.count()
    avg_sale       = (total_revenue / total_count) if total_count else 0
    return {
        'total_revenue':  float(total_revenue),
        'total_discount': float(total_discount),
        'total_count':    total_count,
        'avg_sale':       float(avg_sale),
    }


# ── OTP ───────────────────────────────────────────────────────────────────────

def generate_otp():
    return f"{random.randint(0, 999999):06d}"


def send_otp_email(email, otp):
    try:
        msg = Message(subject='Your Eventry POS Verification Code', recipients=[email])
        msg.html = f"""
        <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;
                    border:1px solid #e0e0e0;border-radius:8px;">
          <h2 style="color:#212529;margin-bottom:4px;">Eventry POS</h2>
          <p style="color:#6c757d;font-size:14px;margin-top:0">Email Verification</p>
          <hr style="border:none;border-top:1px solid #e0e0e0;margin:16px 0"/>
          <p style="font-size:15px;">Use the code below to verify your email address:</p>
          <div style="background:#f8f9fa;border-radius:8px;padding:20px;text-align:center;
                      font-size:36px;font-weight:700;letter-spacing:10px;color:#198754;
                      font-family:monospace;margin:16px 0;">{otp}</div>
          <p style="font-size:13px;color:#6c757d;">Expires in <strong>10 minutes</strong>.</p>
          <hr style="border:none;border-top:1px solid #e0e0e0;margin:16px 0"/>
          <p style="font-size:12px;color:#adb5bd;margin:0">If you did not request this, ignore this email.</p>
        </div>"""
        mail.send(msg)
        return True
    except Exception as exc:
        current_app.logger.error(f'OTP email failed to {email}: {exc}')
        return False


# ── Business approval notification ───────────────────────────────────────────

def send_approval_email(business):
    try:
        login_url = f"{current_app.config.get('APP_BASE_URL', 'http://localhost:5000')}/{business.slug}/login"
        msg = Message(subject=f'Welcome to Eventry POS, {business.name}! Your account is ready',
                      recipients=[business.email])
        msg.html = f"""
        <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px;
                    border:1px solid #e0e0e0;border-radius:8px;">
          <h2 style="color:#198754;margin-bottom:4px;">Congratulations, {business.name}! 🎉</h2>
          <p style="color:#6c757d;font-size:14px;margin-top:0">Your Eventry POS account is now active</p>
          <hr style="border:none;border-top:1px solid #e0e0e0;margin:16px 0"/>

          <p style="font-size:15px;">Hello,</p>
          <p style="font-size:15px;">
            Taking the step to structure your business on a proper system is a bold move — and it's
            one that pays off. You now have <strong>{business.name}</strong> running on a growth-ready,
            easy-to-use platform built to take the guesswork out of managing inventory and sales.
          </p>
          <p style="font-size:15px;">
            From today, every sale, every restock, and every product you manage feeds into
            real, valuable information you can act on — so the decisions that matter most for your
            business are backed by data, not guesswork.
          </p>

          <p style="font-size:15px;font-weight:700;margin-bottom:6px;">Your <strong>14-day free trial</strong> has started — no payment required.</p>

          <div style="text-align:center;margin:24px 0;">
            <a href="{login_url}" style="background:#198754;color:#fff;padding:12px 32px;
               border-radius:8px;text-decoration:none;font-weight:700;font-size:16px;display:inline-block;">
              Sign In to Your Account
            </a>
          </div>
          <p style="font-size:13px;color:#6c757d;text-align:center;margin-top:-12px;">
            or copy this link: <span style="color:#0d6efd;">{login_url}</span>
          </p>

          <hr style="border:none;border-top:1px solid #e0e0e0;margin:20px 0"/>
          <p style="font-size:13px;color:#6c757d;">
            This sign-in page is also where every other account for your business — store staff,
            warehouse keepers, and sales reps — will be created and managed going forward.
          </p>
          <p style="font-size:14px;margin-top:20px">Welcome aboard — here's to growing {business.name}!<br/>— The Eventry POS Team</p>
        </div>"""
        mail.send(msg)
    except Exception as exc:
        current_app.logger.error(f'Approval email failed: {exc}')


# ── Subscription upgrade reminder ─────────────────────────────────────────────

def _upgrade_url(app, business):
    base = app.config.get('APP_BASE_URL', 'http://localhost:5000')
    return f"{base}/{business.slug}/admin/upgrade"


def _send_upgrade_reminder(app, business, days_left, is_trial=True):
    """Send a timed upgrade reminder email."""
    try:
        upgrade_url = _upgrade_url(app, business)
        subject_context = "Free Trial" if is_trial else "subscription"
        if days_left == 0:
            urgency = "expires TODAY"
            color   = "#dc3545"
        elif days_left <= 2:
            urgency = f"expires in {days_left} day{'s' if days_left > 1 else ''}"
            color   = "#fd7e14"
        else:
            urgency = f"expires in {days_left} days"
            color   = "#ffc107"

        msg = Message(
            subject=f'[Eventry POS] Your {subject_context} {urgency} — Upgrade now',
            recipients=[business.email],
        )
        msg.html = f"""
        <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px;
                    border:2px solid {color};border-radius:8px;">
          <h2 style="color:{color};">⏰ Action Required</h2>
          <p>Hello,</p>
          <p>Your <strong>Eventry POS {subject_context}</strong> for
             <strong>{business.name}</strong> <strong style="color:{color};">{urgency}</strong>.</p>
          <p>After expiry, your account will revert to the <strong>Free plan</strong>, which includes:</p>
          <ul style="color:#6c757d;font-size:14px;">
            <li>Max 20 products</li>
            <li>Max 2 users</li>
            <li>No expense tracking or P&amp;L reports</li>
            <li>No automated reorder alerts</li>
            <li>No camera scanner or price history</li>
          </ul>
          <p>Upgrade to keep all your features uninterrupted:</p>
          <div style="text-align:center;margin:24px 0;">
            <a href="{upgrade_url}" style="background:{color};color:#fff;padding:12px 32px;
               border-radius:8px;text-decoration:none;font-weight:700;font-size:16px;">
              View Upgrade Plans
            </a>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px;margin-top:16px;">
            <tr style="background:#f8f9fa;">
              <th style="padding:8px;text-align:left;border:1px solid #dee2e6">Plan</th>
              <th style="padding:8px;text-align:center;border:1px solid #dee2e6">Monthly</th>
              <th style="padding:8px;text-align:center;border:1px solid #dee2e6">Annual</th>
            </tr>
            <tr>
              <td style="padding:8px;border:1px solid #dee2e6"><strong>Pro</strong></td>
              <td style="padding:8px;text-align:center;border:1px solid #dee2e6">$10 / mo</td>
              <td style="padding:8px;text-align:center;border:1px solid #dee2e6">$100 / yr</td>
            </tr>
            <tr>
              <td style="padding:8px;border:1px solid #dee2e6"><strong>Premium</strong></td>
              <td style="padding:8px;text-align:center;border:1px solid #dee2e6">$20 / mo</td>
              <td style="padding:8px;text-align:center;border:1px solid #dee2e6">$180 / yr</td>
            </tr>
          </table>
          <p style="font-size:12px;color:#adb5bd;margin-top:20px;">
            — Eventry POS Team
          </p>
        </div>"""
        mail.send(msg)
        return True
    except Exception as exc:
        app.logger.error(f'Upgrade reminder failed for {business.name}: {exc}')
        return False


# ── Reorder alerts ────────────────────────────────────────────────────────────

def send_reorder_alert_email(business, low_products):
    try:
        rows = ''.join(
            f"<tr><td style='padding:6px 10px'>{p.name}</td>"
            f"<td style='padding:6px 10px;text-align:center'>{p.quantity_in_stock}</td>"
            f"<td style='padding:6px 10px;text-align:center'>{p.reorder_level}</td>"
            f"<td style='padding:6px 10px;font-family:monospace'>{p.barcode}</td></tr>"
            for p in low_products
        )
        msg = Message(subject=f'[Eventry POS] Low Stock Alert — {business.name}',
                      recipients=[business.email])
        msg.html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:24px;
                    border:1px solid #e0e0e0;border-radius:8px;">
          <h2 style="color:#dc3545;">Low Stock Alert</h2>
          <p>Hello,</p>
          <p>The following products in <strong>{business.name}</strong> have reached
             or fallen below their reorder level:</p>
          <table style="width:100%;border-collapse:collapse;margin-top:12px;font-size:14px;">
            <thead>
              <tr style="background:#f8f9fa;">
                <th style="padding:8px 10px;text-align:left;border-bottom:2px solid #dee2e6">Product</th>
                <th style="padding:8px 10px;text-align:center;border-bottom:2px solid #dee2e6">In Stock</th>
                <th style="padding:8px 10px;text-align:center;border-bottom:2px solid #dee2e6">Reorder At</th>
                <th style="padding:8px 10px;border-bottom:2px solid #dee2e6">Barcode</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
          <p style="margin-top:16px;color:#6c757d;font-size:13px;">
            You will receive another reminder in 24 hours if stock has not been updated.
          </p>
        </div>"""
        mail.send(msg)
        return True
    except Exception as exc:
        current_app.logger.error(f'Reorder alert failed for {business.name}: {exc}')
        return False


# ── Scheduler jobs ────────────────────────────────────────────────────────────

def check_reorder_alerts(app):
    """Hourly: send low-stock alerts per business (once per 24 h per product)."""
    with app.app_context():
        from .models import Business, Product
        from sqlalchemy import or_

        cutoff = datetime.utcnow() - timedelta(hours=24)
        for biz in Business.query.filter_by(status='active').all():
            low = Product.query.filter(
                Product.business_id == biz.id,
                Product.is_active == True,
                Product.quantity_in_stock <= Product.reorder_level,
                or_(Product.reorder_alert_sent_at == None,
                    Product.reorder_alert_sent_at < cutoff)
            ).all()
            if not low:
                continue
            # Only send if plan allows reorder alerts
            sub = biz.subscription
            if sub and not sub.features.get('has_reorder_alerts'):
                continue
            if send_reorder_alert_email(biz, low):
                now = datetime.utcnow()
                for p in low:
                    p.reorder_alert_sent_at = now
                db.session.commit()


def check_subscriptions(app):
    """Hourly: expire stale trials/plans, send 5d/2d/1d upgrade reminders."""
    with app.app_context():
        from .models import Subscription, Business

        now   = datetime.utcnow()
        subs  = Subscription.query.join(Business).filter(
            Business.status == 'active'
        ).all()
        dirty = False

        for sub in subs:
            biz = sub.business

            # ── Trial reminders & expiry ──────────────────────────────────────
            if sub.status == 'trialing' and sub.trial_ends_at:
                days = sub.trial_days_left

                if days <= 5 and not sub.trial_r5d_sent:
                    if _send_upgrade_reminder(app, biz, days, is_trial=True):
                        sub.trial_r5d_sent = True
                        dirty = True

                if days <= 2 and not sub.trial_r2d_sent:
                    if _send_upgrade_reminder(app, biz, days, is_trial=True):
                        sub.trial_r2d_sent = True
                        dirty = True

                if days <= 0 and not sub.trial_r1d_sent:
                    if _send_upgrade_reminder(app, biz, 0, is_trial=True):
                        sub.trial_r1d_sent = True
                        dirty = True

                # Expire the trial
                if now > sub.trial_ends_at and sub.status == 'trialing':
                    sub.status = 'expired'
                    dirty = True

            # ── Paid plan reminders & expiry ──────────────────────────────────
            if sub.status == 'active' and sub.period_end:
                days = sub.days_until_expiry or 0

                if days <= 5 and not sub.plan_r5d_sent:
                    if _send_upgrade_reminder(app, biz, days, is_trial=False):
                        sub.plan_r5d_sent = True
                        dirty = True

                if days <= 2 and not sub.plan_r2d_sent:
                    if _send_upgrade_reminder(app, biz, days, is_trial=False):
                        sub.plan_r2d_sent = True
                        dirty = True

                if days <= 0 and not sub.plan_r1d_sent:
                    if _send_upgrade_reminder(app, biz, 0, is_trial=False):
                        sub.plan_r1d_sent = True
                        dirty = True

                # Expire the paid plan
                if now > sub.period_end:
                    sub.status = 'expired'
                    sub.plan   = 'free'
                    dirty = True

        if dirty:
            db.session.commit()
