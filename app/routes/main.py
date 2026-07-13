from datetime import datetime, timedelta
from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, session)
from ..models import Business, OtpVerification
from ..extensions import db
from ..utils import slugify, generate_otp, send_otp_email, send_registration_received_email

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def landing():
    return render_template('landing.html')


# ─── Step 1: Enter email → send OTP ─────────────────────────────────────────

@main_bp.route('/register', methods=['GET', 'POST'])
def register_step1():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email:
            flash('Email address is required.', 'danger')
            return render_template('auth/register_step1.html')

        if Business.query.filter_by(email=email).first():
            flash('A business account with this email already exists.', 'danger')
            return render_template('auth/register_step1.html')

        # Invalidate any previous unexpired OTPs for this email
        OtpVerification.query.filter_by(email=email, verified=False).delete()

        otp = generate_otp()
        record = OtpVerification(
            email=email,
            otp=otp,
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        )
        db.session.add(record)
        db.session.commit()

        ok = send_otp_email(email, otp)
        if not ok:
            flash('Could not send verification email. Check mail configuration.', 'danger')
            return render_template('auth/register_step1.html')

        session['reg_email'] = email
        flash(f'A 6-digit verification code has been sent to {email}.', 'info')
        return redirect(url_for('main.verify_otp'))

    return render_template('auth/register_step1.html')


# ─── Step 2: Enter OTP ───────────────────────────────────────────────────────

@main_bp.route('/register/verify', methods=['GET', 'POST'])
def verify_otp():
    email = session.get('reg_email')
    if not email:
        return redirect(url_for('main.register_step1'))

    if request.method == 'POST':
        entered = request.form.get('otp', '').strip()

        record = (OtpVerification.query
                  .filter_by(email=email, verified=False)
                  .order_by(OtpVerification.created_at.desc())
                  .first())

        if not record:
            flash('No OTP found. Please request a new one.', 'danger')
            return redirect(url_for('main.register_step1'))

        if record.is_expired:
            flash('Your code has expired. Please request a new one.', 'warning')
            return redirect(url_for('main.register_step1'))

        if entered != record.otp:
            flash('Incorrect code. Please try again.', 'danger')
            return render_template('auth/verify_otp.html', email=email)

        # Mark verified
        record.verified = True
        db.session.commit()
        session['reg_email_verified'] = True
        return redirect(url_for('main.register_complete'))

    return render_template('auth/verify_otp.html', email=email)


@main_bp.route('/register/resend', methods=['POST'])
def resend_otp():
    email = session.get('reg_email')
    if not email:
        return redirect(url_for('main.register_step1'))

    OtpVerification.query.filter_by(email=email, verified=False).delete()
    otp = generate_otp()
    record = OtpVerification(
        email=email,
        otp=otp,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    )
    db.session.add(record)
    db.session.commit()
    send_otp_email(email, otp)
    flash('A new code has been sent.', 'info')
    return redirect(url_for('main.verify_otp'))


# ─── Step 3: Full registration form ─────────────────────────────────────────

@main_bp.route('/register/complete', methods=['GET', 'POST'])
def register_complete():
    email = session.get('reg_email')
    if not email or not session.get('reg_email_verified'):
        return redirect(url_for('main.register_step1'))

    if request.method == 'POST':
        name           = request.form.get('name', '').strip()
        phone          = request.form.get('phone', '').strip()
        address        = request.form.get('address', '').strip()
        admin_username = request.form.get('admin_username', '').strip()
        admin_password = request.form.get('admin_password', '').strip()
        admin_first    = request.form.get('admin_first', '').strip()
        admin_last     = request.form.get('admin_last', '').strip()
        currency       = request.form.get('currency', 'NGN').strip()
        currency_symbol = request.form.get('currency_symbol', '₦').strip()

        errors = []
        if not name:
            errors.append('Business name is required.')
        if not admin_username:
            errors.append('Admin username is required.')
        if not admin_password or len(admin_password) < 6:
            errors.append('Admin password must be at least 6 characters.')
        if Business.query.filter_by(email=email).first():
            errors.append('A business with this email already exists.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('auth/register_business.html',
                                   form=request.form, email=email)

        slug = slugify(name)
        base, counter = slug, 1
        while Business.query.filter_by(slug=slug).first():
            slug = f'{base}-{counter}'
            counter += 1

        from ..models import User
        business = Business(
            name=name, slug=slug, email=email,
            phone=phone, address=address,
            currency=currency, currency_symbol=currency_symbol,
            status='pending',
        )
        db.session.add(business)
        db.session.flush()

        admin = User(
            business_id=business.id,
            username=admin_username,
            email=email,
            role='admin',
            first_name=admin_first,
            last_name=admin_last,
        )
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()

        send_registration_received_email(business)

        # Clear registration session keys
        session.pop('reg_email', None)
        session.pop('reg_email_verified', None)

        flash(
            'Registration submitted! Your account is pending approval by our team. '
            'You will receive an email once it is activated.',
            'success'
        )
        return redirect(url_for('main.landing'))

    return render_template('auth/register_business.html',
                           form={}, email=email)


# Keep the old /register URL working (backward compat redirect)
@main_bp.route('/register-old')
def register_business():
    return redirect(url_for('main.register_step1'))
