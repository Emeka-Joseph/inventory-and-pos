from datetime import datetime, timedelta, date as date_type
from decimal import Decimal
import requests as http_requests
from flask import Blueprint, render_template, redirect, url_for, request, flash, g, jsonify, abort, current_app
from flask_login import login_required, current_user
from sqlalchemy import func, extract
from ..extensions import db
from ..models import (Business, User, Product, Category, StockEntry,
                      Sale, SaleItem, WorkSession, PriceHistory, Expense, Subscription)
from ..config import PLAN_LIMITS
from ..utils import load_business_or_404, admin_required, get_date_range, sales_summary, slugify

admin_bp = Blueprint('admin', __name__)


def _load_biz(slug):
    biz = load_business_or_404(slug)
    if not current_user.is_authenticated or current_user.business_id != biz.id or current_user.role != 'admin':
        abort(403)
    g.business = biz
    return biz


# ─── Dashboard ───────────────────────────────────────────────────────────────

@admin_bp.route('/<slug>/admin/')
@login_required
def dashboard(slug):
    biz = _load_biz(slug)
    today_start, today_end = get_date_range('today')
    week_start, week_end = get_date_range('week')
    month_start, month_end = get_date_range('month')

    today_summary = sales_summary(biz.id, today_start, today_end)
    week_summary = sales_summary(biz.id, week_start, week_end)
    month_summary = sales_summary(biz.id, month_start, month_end)

    total_products = Product.query.filter_by(business_id=biz.id, is_active=True).count()
    low_stock = Product.query.filter(
        Product.business_id == biz.id,
        Product.is_active == True,
        Product.quantity_in_stock <= Product.reorder_level
    ).count()
    active_sessions = WorkSession.query.filter_by(business_id=biz.id, is_active=True).count()

    stock_value = db.session.query(
        func.sum(Product.cost_price * Product.quantity_in_stock)
    ).filter_by(business_id=biz.id, is_active=True).scalar() or 0

    # last 7 days chart data
    chart_labels = []
    chart_data = []
    for i in range(6, -1, -1):
        d = (datetime.utcnow() - timedelta(days=i)).date()
        label = d.strftime('%a %d')
        start = datetime.combine(d, datetime.min.time())
        end = datetime.combine(d, datetime.max.time())
        rev = db.session.query(func.sum(Sale.total_amount)).filter(
            Sale.business_id == biz.id,
            Sale.created_at >= start,
            Sale.created_at <= end
        ).scalar() or 0
        chart_labels.append(label)
        chart_data.append(float(rev))

    recent_sales = Sale.query.filter_by(business_id=biz.id).order_by(Sale.created_at.desc()).limit(10).all()

    return render_template('admin/dashboard.html',
                           business=biz,
                           today=today_summary,
                           week=week_summary,
                           month=month_summary,
                           total_products=total_products,
                           low_stock=low_stock,
                           active_sessions=active_sessions,
                           stock_value=float(stock_value),
                           chart_labels=chart_labels,
                           chart_data=chart_data,
                           recent_sales=recent_sales)


# ─── Products ────────────────────────────────────────────────────────────────

@admin_bp.route('/<slug>/admin/products')
@login_required
def products(slug):
    biz = _load_biz(slug)
    cats = Category.query.filter_by(business_id=biz.id).all()
    cat_id = request.args.get('cat', type=int)
    q = request.args.get('q', '').strip()
    query = Product.query.filter_by(business_id=biz.id)
    if cat_id:
        query = query.filter_by(category_id=cat_id)
    if q:
        query = query.filter(Product.name.ilike(f'%{q}%'))
    prods = query.order_by(Product.name).all()
    return render_template('admin/products.html', business=biz, products=prods,
                           categories=cats, selected_cat=cat_id, q=q)


@admin_bp.route('/<slug>/admin/products/new', methods=['GET', 'POST'])
@login_required
def new_product(slug):
    biz = _load_biz(slug)
    sub = biz.subscription
    features = sub.features if sub else PLAN_LIMITS['free']
    max_products = features.get('max_products')
    if max_products is not None:
        current_count = Product.query.filter_by(business_id=biz.id, is_active=True).count()
        if current_count >= max_products:
            flash(f'Your {features["label"]} plan allows up to {max_products} products. '
                  f'Upgrade to add more.', 'warning')
            return redirect(url_for('admin.upgrade', slug=slug))

    cats = Category.query.filter_by(business_id=biz.id).all()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Product name is required.', 'danger')
            return render_template('admin/product_form.html', business=biz, categories=cats, product=None)

        barcode = request.form.get('barcode', '').strip()
        if not barcode:
            flash('Barcode is required.', 'danger')
            return render_template('admin/product_form.html', business=biz, categories=cats, product=None)
        if Product.query.filter_by(business_id=biz.id, barcode=barcode).first():
            flash(f'Barcode "{barcode}" is already assigned to another product.', 'danger')
            return render_template('admin/product_form.html', business=biz, categories=cats, product=None)

        mfg_raw = request.form.get('manufacture_date', '').strip()
        exp_raw = request.form.get('expiry_date', '').strip()
        try:
            mfg_date = datetime.strptime(mfg_raw, '%Y-%m-%d').date() if mfg_raw else None
            exp_date = datetime.strptime(exp_raw, '%Y-%m-%d').date() if exp_raw else None
        except ValueError:
            flash('Invalid date format.', 'danger')
            return render_template('admin/product_form.html', business=biz, categories=cats, product=None)

        prod = Product(
            business_id=biz.id,
            name=name,
            barcode=barcode,
            manufacture_date=mfg_date,
            expiry_date=exp_date,
            description=request.form.get('description', '').strip(),
            unit_price=Decimal(request.form.get('unit_price', '0') or '0'),
            cost_price=Decimal(request.form.get('cost_price', '0') or '0'),
            reorder_level=int(request.form.get('reorder_level', 5) or 5),
            unit=request.form.get('unit', 'piece').strip(),
            category_id=request.form.get('category_id', type=int) or None,
        )
        db.session.add(prod)
        db.session.commit()
        flash(f'Product "{name}" created.', 'success')
        return redirect(url_for('admin.products', slug=slug))
    return render_template('admin/product_form.html', business=biz, categories=cats, product=None)


@admin_bp.route('/<slug>/admin/products/<int:product_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_product(slug, product_id):
    biz = _load_biz(slug)
    prod = Product.query.filter_by(id=product_id, business_id=biz.id).first_or_404()
    cats = Category.query.filter_by(business_id=biz.id).all()

    if request.method == 'POST':
        old_price = prod.unit_price
        prod.name = request.form.get('name', prod.name).strip()
        new_barcode = request.form.get('barcode', '').strip()
        if not new_barcode:
            flash('Barcode is required.', 'danger')
            return render_template('admin/product_form.html', business=biz, categories=cats, product=prod)
        if new_barcode != prod.barcode:
            conflict = Product.query.filter_by(business_id=biz.id, barcode=new_barcode).first()
            if conflict:
                flash(f'Barcode "{new_barcode}" is already assigned to "{conflict.name}".', 'danger')
                return render_template('admin/product_form.html', business=biz, categories=cats, product=prod)
        prod.barcode = new_barcode
        prod.description = request.form.get('description', '').strip()
        prod.unit_price = Decimal(request.form.get('unit_price', str(prod.unit_price)) or '0')
        prod.cost_price = Decimal(request.form.get('cost_price', str(prod.cost_price)) or '0')
        prod.reorder_level = int(request.form.get('reorder_level', prod.reorder_level) or prod.reorder_level)
        prod.unit = request.form.get('unit', prod.unit).strip()
        prod.category_id = request.form.get('category_id', type=int) or None
        prod.is_active = bool(request.form.get('is_active'))
        mfg_raw = request.form.get('manufacture_date', '').strip()
        exp_raw = request.form.get('expiry_date', '').strip()
        try:
            prod.manufacture_date = datetime.strptime(mfg_raw, '%Y-%m-%d').date() if mfg_raw else None
            prod.expiry_date = datetime.strptime(exp_raw, '%Y-%m-%d').date() if exp_raw else None
        except ValueError:
            flash('Invalid date format.', 'danger')
            return render_template('admin/product_form.html', business=biz, categories=cats, product=prod)
        prod.updated_at = datetime.utcnow()

        if prod.unit_price != old_price:
            ph = PriceHistory(
                product_id=prod.id,
                old_price=old_price,
                new_price=prod.unit_price,
                changed_by=current_user.id,
            )
            db.session.add(ph)

        db.session.commit()
        flash(f'Product "{prod.name}" updated.', 'success')
        return redirect(url_for('admin.products', slug=slug))

    return render_template('admin/product_form.html', business=biz, categories=cats, product=prod)


@admin_bp.route('/<slug>/admin/products/<int:product_id>/toggle', methods=['POST'])
@login_required
def toggle_product(slug, product_id):
    biz = _load_biz(slug)
    prod = Product.query.filter_by(id=product_id, business_id=biz.id).first_or_404()
    prod.is_active = not prod.is_active
    db.session.commit()
    status = 'activated' if prod.is_active else 'deactivated'
    flash(f'Product "{prod.name}" {status}.', 'success')
    return redirect(url_for('admin.products', slug=slug))


# ─── Categories ──────────────────────────────────────────────────────────────

@admin_bp.route('/<slug>/admin/categories/new', methods=['POST'])
@login_required
def new_category(slug):
    biz = _load_biz(slug)
    name = request.form.get('name', '').strip()
    if name:
        cat = Category(business_id=biz.id, name=name)
        db.session.add(cat)
        db.session.commit()
        flash(f'Category "{name}" added.', 'success')
    return redirect(request.referrer or url_for('admin.products', slug=slug))


# ─── Stock ───────────────────────────────────────────────────────────────────

@admin_bp.route('/<slug>/admin/stock')
@login_required
def stock(slug):
    biz = _load_biz(slug)
    q = request.args.get('q', '').strip()
    entries = (StockEntry.query
               .filter_by(business_id=biz.id)
               .join(Product)
               .order_by(StockEntry.created_at.desc()))
    if q:
        entries = entries.filter(Product.name.ilike(f'%{q}%'))
    entries = entries.limit(200).all()
    all_products = Product.query.filter_by(business_id=biz.id, is_active=True).order_by(Product.name).all()
    low_stock_items = [p for p in all_products if p.is_low_stock]
    return render_template('admin/stock.html', business=biz, entries=entries,
                           all_products=all_products, low_stock_items=low_stock_items, q=q)


@admin_bp.route('/<slug>/admin/stock/adjust', methods=['POST'])
@login_required
def adjust_stock(slug):
    """Admin-only direct stock adjustment (not an immutable stock entry)."""
    biz = _load_biz(slug)
    product_id = request.form.get('product_id', type=int)
    quantity = request.form.get('quantity', type=int)
    reason = request.form.get('reason', '').strip()

    prod = Product.query.filter_by(id=product_id, business_id=biz.id).first_or_404()
    if quantity is None:
        flash('Quantity is required.', 'danger')
        return redirect(url_for('admin.stock', slug=slug))

    prod.quantity_in_stock += quantity
    if prod.quantity_in_stock < 0:
        prod.quantity_in_stock = 0

    entry = StockEntry(
        business_id=biz.id,
        product_id=prod.id,
        user_id=current_user.id,
        quantity=quantity,
        notes=f'[ADMIN ADJUSTMENT] {reason}',
    )
    db.session.add(entry)
    db.session.commit()
    flash(f'Stock adjusted for "{prod.name}".', 'success')
    return redirect(url_for('admin.stock', slug=slug))


# ─── Sales History ───────────────────────────────────────────────────────────

@admin_bp.route('/<slug>/admin/sales')
@login_required
def sales_history(slug):
    biz = _load_biz(slug)
    period = request.args.get('period', 'today')
    start, end = get_date_range(period)
    user_id = request.args.get('user_id', type=int)
    payment = request.args.get('payment', '').strip()

    query = Sale.query.filter(
        Sale.business_id == biz.id,
        Sale.created_at >= start,
        Sale.created_at <= end
    )
    if user_id:
        query = query.filter_by(user_id=user_id)
    if payment:
        query = query.filter_by(payment_method=payment)

    sale_list = query.order_by(Sale.created_at.desc()).all()
    summary = sales_summary(biz.id, start, end)
    sellers = User.query.filter_by(business_id=biz.id, is_active=True).all()

    return render_template('admin/sales.html', business=biz, sales=sale_list,
                           summary=summary, period=period, sellers=sellers,
                           selected_user=user_id, selected_payment=payment,
                           start=start, end=end)


@admin_bp.route('/<slug>/admin/sales/<int:sale_id>')
@login_required
def sale_detail(slug, sale_id):
    biz = _load_biz(slug)
    sale = Sale.query.filter_by(id=sale_id, business_id=biz.id).first_or_404()
    return render_template('admin/sale_detail.html', business=biz, sale=sale)


# ─── Reports / Analytics ─────────────────────────────────────────────────────

@admin_bp.route('/<slug>/admin/reports')
@login_required
def reports(slug):
    biz = _load_biz(slug)
    sub = biz.subscription
    features = sub.features if sub else PLAN_LIMITS['free']
    if not features.get('has_full_reports'):
        flash('Full financial reports are not available on your current plan. Upgrade to unlock them.', 'warning')
        return redirect(url_for('admin.upgrade', slug=slug))
    period = request.args.get('period', 'month')
    start, end = get_date_range(period)

    summary = sales_summary(biz.id, start, end)

    # ── COGS: qty_sold × current cost_price per product ──────────────────────
    cogs_raw = db.session.query(
        func.sum(SaleItem.quantity * Product.cost_price)
    ).join(Product, SaleItem.product_id == Product.id
    ).join(Sale, SaleItem.sale_id == Sale.id
    ).filter(
        Sale.business_id == biz.id,
        Sale.created_at >= start,
        Sale.created_at <= end
    ).scalar() or 0

    # ── Expenses for selected period ──────────────────────────────────────────
    period_expenses = (Expense.query
                       .filter(Expense.business_id == biz.id,
                               Expense.expense_date >= start.date(),
                               Expense.expense_date <= end.date())
                       .order_by(Expense.expense_date.desc()).all())

    total_expenses_raw = sum((e.amount for e in period_expenses), Decimal('0'))

    expense_by_cat = {}
    for e in period_expenses:
        expense_by_cat[e.category] = expense_by_cat.get(e.category, Decimal('0')) + e.amount

    # ── P&L ──────────────────────────────────────────────────────────────────
    revenue       = Decimal(str(summary['total_revenue']))
    cogs          = Decimal(str(cogs_raw))
    gross_profit  = revenue - cogs
    total_expenses = total_expenses_raw
    net_profit    = gross_profit - total_expenses
    gross_margin  = float(gross_profit / revenue * 100) if revenue else 0.0
    net_margin    = float(net_profit   / revenue * 100) if revenue else 0.0

    # ── Top products ──────────────────────────────────────────────────────────
    top_products = (db.session.query(
        SaleItem.product_name,
        func.sum(SaleItem.subtotal).label('revenue'),
        func.sum(SaleItem.quantity).label('qty_sold')
    ).join(Sale)
    .filter(Sale.business_id == biz.id, Sale.created_at >= start, Sale.created_at <= end)
    .group_by(SaleItem.product_name)
    .order_by(func.sum(SaleItem.subtotal).desc())
    .limit(10).all())

    # ── Sales by rep ──────────────────────────────────────────────────────────
    sales_by_rep = (db.session.query(
        User.first_name, User.last_name, User.username,
        func.count(Sale.id).label('count'),
        func.sum(Sale.total_amount).label('revenue')
    ).join(Sale, Sale.user_id == User.id)
    .filter(Sale.business_id == biz.id, Sale.created_at >= start, Sale.created_at <= end)
    .group_by(User.id)
    .order_by(func.sum(Sale.total_amount).desc()).all())

    # ── Payment breakdown ─────────────────────────────────────────────────────
    payment_breakdown = (db.session.query(
        Sale.payment_method,
        func.count(Sale.id).label('count'),
        func.sum(Sale.total_amount).label('revenue')
    ).filter(Sale.business_id == biz.id, Sale.created_at >= start, Sale.created_at <= end)
    .group_by(Sale.payment_method).all())

    # ── Revenue trend chart (for Sales Analytics tab) ─────────────────────────
    chart_days = 7 if period in ('today', 'week') else (30 if period == 'month' else 12)
    chart_labels, chart_revenue = [], []
    if period == 'year':
        for m in range(1, 13):
            ms = datetime(datetime.utcnow().year, m, 1)
            me = (datetime(datetime.utcnow().year, m + 1, 1) if m < 12
                  else datetime(datetime.utcnow().year + 1, 1, 1)) - timedelta(seconds=1)
            rev = db.session.query(func.sum(Sale.total_amount)).filter(
                Sale.business_id == biz.id, Sale.created_at >= ms, Sale.created_at <= me
            ).scalar() or 0
            chart_labels.append(ms.strftime('%b'))
            chart_revenue.append(float(rev))
    else:
        for i in range(chart_days - 1, -1, -1):
            d = (datetime.utcnow() - timedelta(days=i)).date()
            ds = datetime.combine(d, datetime.min.time())
            de = datetime.combine(d, datetime.max.time())
            rev = db.session.query(func.sum(Sale.total_amount)).filter(
                Sale.business_id == biz.id, Sale.created_at >= ds, Sale.created_at <= de
            ).scalar() or 0
            chart_labels.append(d.strftime('%m/%d'))
            chart_revenue.append(float(rev))

    # ── Yearly P&L analysis (always current calendar year) ───────────────────
    current_year = datetime.utcnow().year
    yearly_labels, yearly_rev, yearly_cogs_l = [], [], []
    yearly_exp_l, yearly_gross_l, yearly_net_l = [], [], []

    for m in range(1, 13):
        ms = datetime(current_year, m, 1)
        me = (datetime(current_year, m + 1, 1) if m < 12
              else datetime(current_year + 1, 1, 1)) - timedelta(seconds=1)

        m_rev = float(db.session.query(func.sum(Sale.total_amount)).filter(
            Sale.business_id == biz.id, Sale.created_at >= ms, Sale.created_at <= me
        ).scalar() or 0)

        m_cogs = float(db.session.query(
            func.sum(SaleItem.quantity * Product.cost_price)
        ).join(Product, SaleItem.product_id == Product.id
        ).join(Sale, SaleItem.sale_id == Sale.id
        ).filter(Sale.business_id == biz.id, Sale.created_at >= ms, Sale.created_at <= me
        ).scalar() or 0)

        m_exp = float(db.session.query(func.sum(Expense.amount)).filter(
            Expense.business_id == biz.id,
            Expense.expense_date >= ms.date(),
            Expense.expense_date <= me.date()
        ).scalar() or 0)

        yearly_labels.append(ms.strftime('%b'))
        yearly_rev.append(m_rev)
        yearly_cogs_l.append(m_cogs)
        yearly_exp_l.append(m_exp)
        yearly_gross_l.append(round(m_rev - m_cogs, 2))
        yearly_net_l.append(round(m_rev - m_cogs - m_exp, 2))

    return render_template('admin/reports.html',
        business=biz, period=period, start=start, end=end,
        summary=summary,
        cogs=float(cogs),
        gross_profit=float(gross_profit),
        total_expenses=float(total_expenses),
        net_profit=float(net_profit),
        gross_margin=gross_margin,
        net_margin=net_margin,
        expense_by_cat={k: float(v) for k, v in expense_by_cat.items()},
        period_expenses=period_expenses,
        expense_categories=Expense.CATEGORIES,
        top_products=top_products,
        sales_by_rep=sales_by_rep,
        payment_breakdown=payment_breakdown,
        chart_labels=chart_labels,
        chart_revenue=chart_revenue,
        current_year=current_year,
        yearly_labels=yearly_labels,
        yearly_rev=yearly_rev,
        yearly_cogs=yearly_cogs_l,
        yearly_exp=yearly_exp_l,
        yearly_gross=yearly_gross_l,
        yearly_net=yearly_net_l,
    )


# ─── Expenses ─────────────────────────────────────────────────────────────────

@admin_bp.route('/<slug>/admin/expenses/add', methods=['POST'])
@login_required
def add_expense(slug):
    biz = _load_biz(slug)
    period = request.form.get('period', 'month')
    category = request.form.get('category', '').strip()
    description = request.form.get('description', '').strip()
    amount_raw = request.form.get('amount', '').strip()
    date_raw = request.form.get('expense_date', '').strip()

    errors = []
    if not category:
        errors.append('Category is required.')
    try:
        amount = Decimal(amount_raw)
        if amount <= 0:
            raise ValueError
    except Exception:
        errors.append('Enter a valid positive amount.')
        amount = Decimal('0')
    try:
        exp_date = datetime.strptime(date_raw, '%Y-%m-%d').date() if date_raw else date_type.today()
    except ValueError:
        errors.append('Invalid date.')
        exp_date = date_type.today()

    if errors:
        for e in errors:
            flash(e, 'danger')
    else:
        db.session.add(Expense(
            business_id=biz.id,
            category=category,
            description=description,
            amount=amount,
            expense_date=exp_date,
            recorded_by=current_user.id,
        ))
        db.session.commit()
        flash(f'Expense recorded: {category} — {biz.currency_symbol}{amount:,.2f}', 'success')

    return redirect(url_for('admin.reports', slug=slug, period=period, t='fin'))


@admin_bp.route('/<slug>/admin/expenses/<int:expense_id>/delete', methods=['POST'])
@login_required
def delete_expense(slug, expense_id):
    biz = _load_biz(slug)
    exp = Expense.query.filter_by(id=expense_id, business_id=biz.id).first_or_404()
    period = request.form.get('period', 'month')
    db.session.delete(exp)
    db.session.commit()
    flash('Expense deleted.', 'success')
    return redirect(url_for('admin.reports', slug=slug, period=period, t='fin'))


# ─── Users ───────────────────────────────────────────────────────────────────

@admin_bp.route('/<slug>/admin/users')
@login_required
def users(slug):
    biz = _load_biz(slug)
    user_list = User.query.filter_by(business_id=biz.id).order_by(User.role, User.username).all()
    return render_template('admin/users.html', business=biz, users=user_list)


@admin_bp.route('/<slug>/admin/users/new', methods=['GET', 'POST'])
@login_required
def new_user(slug):
    biz = _load_biz(slug)
    sub = biz.subscription
    features = sub.features if sub else PLAN_LIMITS['free']
    max_users = features.get('max_users')
    if max_users is not None:
        current_count = User.query.filter_by(business_id=biz.id, is_active=True).count()
        if current_count >= max_users:
            flash(f'Your {features["label"]} plan allows up to {max_users} users. '
                  f'Upgrade to add more.', 'warning')
            return redirect(url_for('admin.upgrade', slug=slug))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        role = request.form.get('role', 'sales_rep').strip()
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        email = request.form.get('email', '').strip() or None

        errors = []
        if not username:
            errors.append('Username is required.')
        if not password or len(password) < 6:
            errors.append('Password must be at least 6 characters.')
        if role not in ('admin', 'store_keeper', 'sales_rep'):
            errors.append('Invalid role.')
        if User.query.filter_by(business_id=biz.id, username=username).first():
            errors.append(f'Username "{username}" already exists.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/user_form.html', business=biz, user=None)

        user = User(
            business_id=biz.id,
            username=username,
            email=email,
            role=role,
            first_name=first_name,
            last_name=last_name,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(f'User "{username}" ({role}) created.', 'success')
        return redirect(url_for('admin.users', slug=slug))

    return render_template('admin/user_form.html', business=biz, user=None)


@admin_bp.route('/<slug>/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_user(slug, user_id):
    biz = _load_biz(slug)
    user = User.query.filter_by(id=user_id, business_id=biz.id).first_or_404()

    if request.method == 'POST':
        user.first_name = request.form.get('first_name', '').strip()
        user.last_name = request.form.get('last_name', '').strip()
        user.email = request.form.get('email', '').strip() or None
        new_password = request.form.get('password', '').strip()
        if new_password:
            if len(new_password) < 6:
                flash('Password must be at least 6 characters.', 'danger')
                return render_template('admin/user_form.html', business=biz, user=user)
            user.set_password(new_password)
        if user.id != current_user.id:
            user.role = request.form.get('role', user.role)
            user.is_active = bool(request.form.get('is_active'))
        db.session.commit()
        flash(f'User "{user.username}" updated.', 'success')
        return redirect(url_for('admin.users', slug=slug))

    return render_template('admin/user_form.html', business=biz, user=user)


# ─── Work Sessions (Admin view) ──────────────────────────────────────────────

@admin_bp.route('/<slug>/admin/sessions')
@login_required
def sessions(slug):
    biz = _load_biz(slug)
    period = request.args.get('period', 'today')
    start, end = get_date_range(period)
    sess_list = (WorkSession.query
                 .filter(WorkSession.business_id == biz.id,
                         WorkSession.clock_in >= start,
                         WorkSession.clock_in <= end)
                 .order_by(WorkSession.clock_in.desc())
                 .all())
    return render_template('admin/sessions.html', business=biz, sessions=sess_list, period=period)


# ─── Price History ───────────────────────────────────────────────────────────

@admin_bp.route('/<slug>/admin/price-history')
@login_required
def price_history(slug):
    biz = _load_biz(slug)
    sub = biz.subscription
    features = sub.features if sub else PLAN_LIMITS['free']
    if not features.get('has_price_history'):
        flash('Price history is not available on your current plan. Upgrade to unlock it.', 'warning')
        return redirect(url_for('admin.upgrade', slug=slug))
    history = (PriceHistory.query
               .join(Product)
               .filter(Product.business_id == biz.id)
               .order_by(PriceHistory.changed_at.desc())
               .limit(100).all())
    return render_template('admin/price_history.html', business=biz, history=history)


# ─── Upgrade / Pricing ───────────────────────────────────────────────────────

@admin_bp.route('/<slug>/admin/upgrade')
@login_required
def upgrade(slug):
    biz = _load_biz(slug)
    sub = biz.subscription
    return render_template('admin/upgrade.html', business=biz,
                           sub=sub, PLAN_LIMITS=PLAN_LIMITS)


# ─── Billing (Paystack) ───────────────────────────────────────────────────────

@admin_bp.route('/<slug>/admin/billing/initialize', methods=['POST'])
@login_required
def billing_initialize(slug):
    """Initialize a Paystack transaction and redirect to hosted checkout."""
    biz   = _load_biz(slug)
    plan  = request.form.get('plan', '').strip()
    cycle = request.form.get('billing_cycle', 'monthly').strip()

    if plan not in ('pro', 'premium') or cycle not in ('monthly', 'annual'):
        flash('Invalid plan selection.', 'danger')
        return redirect(url_for('admin.upgrade', slug=slug))

    prices     = current_app.config['PLAN_PRICES']
    amount     = prices[plan][cycle]
    currency   = current_app.config.get('PAYSTACK_CURRENCY', 'USD')
    secret_key = current_app.config.get('PAYSTACK_SECRET_KEY', '')

    if not secret_key:
        flash('Payment is not configured yet. Please contact support.', 'warning')
        return redirect(url_for('admin.upgrade', slug=slug))

    callback = url_for('admin.billing_verify', slug=slug, _external=True)
    payload  = {
        'email':        biz.email,
        'amount':       amount,
        'currency':     currency,
        'callback_url': callback,
        'metadata': {
            'business_id':   biz.id,
            'plan':          plan,
            'billing_cycle': cycle,
            'slug':          slug,
        },
    }

    try:
        resp = http_requests.post(
            'https://api.paystack.co/transaction/initialize',
            json=payload,
            headers={'Authorization': f'Bearer {secret_key}'},
            timeout=15,
        )
        data = resp.json()
    except Exception as exc:
        current_app.logger.error(f'Paystack init error: {exc}')
        flash('Could not reach payment gateway. Please try again.', 'danger')
        return redirect(url_for('admin.upgrade', slug=slug))

    if not data.get('status'):
        msg = data.get('message', 'Unknown error')
        flash(f'Payment initialization failed: {msg}', 'danger')
        return redirect(url_for('admin.upgrade', slug=slug))

    return redirect(data['data']['authorization_url'])


@admin_bp.route('/<slug>/admin/billing/verify')
@login_required
def billing_verify(slug):
    """Paystack redirects here after payment with ?reference=..."""
    biz       = _load_biz(slug)
    reference = request.args.get('reference', '').strip()

    if not reference:
        flash('Payment reference is missing.', 'danger')
        return redirect(url_for('admin.upgrade', slug=slug))

    secret_key = current_app.config.get('PAYSTACK_SECRET_KEY', '')

    try:
        resp = http_requests.get(
            f'https://api.paystack.co/transaction/verify/{reference}',
            headers={'Authorization': f'Bearer {secret_key}'},
            timeout=15,
        )
        data = resp.json()
    except Exception as exc:
        current_app.logger.error(f'Paystack verify error: {exc}')
        flash('Could not verify payment. Please contact support.', 'danger')
        return redirect(url_for('admin.upgrade', slug=slug))

    if not data.get('status') or data['data'].get('status') != 'success':
        flash('Payment was not successful. Please try again.', 'danger')
        return redirect(url_for('admin.upgrade', slug=slug))

    tx   = data['data']
    meta = tx.get('metadata', {})
    plan  = meta.get('plan')
    cycle = meta.get('billing_cycle', 'monthly')

    if plan not in ('pro', 'premium'):
        flash('Unrecognised plan in payment. Please contact support.', 'danger')
        return redirect(url_for('admin.upgrade', slug=slug))

    # Activate subscription
    sub = biz.subscription
    if not sub:
        sub = Subscription(business_id=biz.id)
        db.session.add(sub)

    sub.plan                  = plan
    sub.billing_cycle         = cycle
    sub.status                = 'active'
    sub.period_start          = datetime.utcnow()
    sub.period_end            = (datetime.utcnow() + timedelta(days=365)
                                 if cycle == 'annual'
                                 else datetime.utcnow() + timedelta(days=30))
    sub.plan_r5d_sent         = False
    sub.plan_r2d_sent         = False
    sub.plan_r1d_sent         = False
    sub.paystack_reference    = reference
    customer = tx.get('customer', {})
    if customer.get('customer_code'):
        sub.paystack_customer_code = customer['customer_code']

    db.session.commit()

    flash(f'Payment successful! Your {plan.title()} ({cycle}) plan is now active.', 'success')
    return redirect(url_for('admin.billing_success', slug=slug,
                            plan=plan, cycle=cycle))


@admin_bp.route('/<slug>/admin/billing/success')
@login_required
def billing_success(slug):
    biz   = _load_biz(slug)
    plan  = request.args.get('plan', 'pro')
    cycle = request.args.get('cycle', 'monthly')
    return render_template('admin/billing_success.html',
                           business=biz, plan=plan, cycle=cycle,
                           sub=biz.subscription)
