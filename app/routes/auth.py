from flask import Blueprint, render_template, redirect, url_for, request, flash, g
from flask_login import login_user, logout_user, current_user
from ..models import User, Business
from ..utils import load_business_or_404

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login')
def login_redirect():
    return render_template('auth/no_slug_login.html')


@auth_bp.route('/<slug>/login', methods=['GET', 'POST'])
def login(slug):
    biz = Business.query.filter_by(slug=slug).first_or_404()

    # Show informative page for non-active businesses
    if biz.status == 'pending':
        return render_template('auth/business_status.html', business=biz,
                               status='pending')
    if biz.status == 'suspended':
        return render_template('auth/business_status.html', business=biz,
                               status='suspended')

    if current_user.is_authenticated and current_user.business_id == biz.id:
        return _redirect_by_role(slug, current_user.role)

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        remember = bool(request.form.get('remember'))

        user = User.query.filter_by(
            business_id=biz.id,
            username=username,
            is_active=True
        ).first()

        if user and user.check_password(password):
            if user.role == 'sales_rep':
                flash('Sales reps must use the POS login.', 'warning')
                return redirect(url_for('pos.pos_login', slug=slug))
            login_user(user, remember=remember)
            flash(f'Welcome back, {user.full_name}!', 'success')
            return _redirect_by_role(slug, user.role)

        flash('Invalid username or password.', 'danger')

    return render_template('auth/login.html', business=biz)


@auth_bp.route('/<slug>/logout')
def logout(slug):
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login', slug=slug))


def _redirect_by_role(slug, role):
    if role == 'admin':
        return redirect(url_for('admin.dashboard', slug=slug))
    if role == 'store_keeper':
        return redirect(url_for('warehouse.warehouse', slug=slug))
    return redirect(url_for('pos.pos_home', slug=slug))
