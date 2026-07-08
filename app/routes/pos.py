from datetime import datetime
from decimal import Decimal
from flask import (Blueprint, render_template, redirect, url_for, request,
                   flash, g, abort, jsonify, session)
from flask_login import login_user, logout_user, current_user, login_required
from ..extensions import db
from ..models import User, Product, Category, Sale, SaleItem, WorkSession, Business
from ..utils import load_business_or_404, generate_sale_number

pos_bp = Blueprint('pos', __name__)


def _load_biz(slug):
    biz = load_business_or_404(slug)
    # Warehouse keepers (role='store_keeper') are scoped to the warehouse portal only.
    if current_user.is_authenticated and current_user.role == 'store_keeper':
        abort(403)
    g.business = biz
    return biz


# ─── POS Login (Sales Rep Clock-In) ──────────────────────────────────────────

@pos_bp.route('/<slug>/pos/login', methods=['GET', 'POST'])
def pos_login(slug):
    biz = _load_biz(slug)

    # Already logged in non-sales user accessing POS
    if current_user.is_authenticated and current_user.business_id == biz.id:
        if current_user.role == 'admin':
            return redirect(url_for('pos.pos_home', slug=slug))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        user = User.query.filter_by(
            business_id=biz.id,
            username=username,
            is_active=True
        ).first()

        if user and user.check_password(password):
            login_user(user)
            if user.role == 'sales_rep':
                existing = WorkSession.query.filter_by(user_id=user.id, is_active=True).first()
                if not existing:
                    ws = WorkSession(user_id=user.id, business_id=biz.id)
                    db.session.add(ws)
                    db.session.commit()
                flash(f'Welcome {user.full_name}! You are clocked in.', 'success')
            else:
                flash(f'Welcome back, {user.full_name}!', 'success')
            return redirect(url_for('pos.pos_home', slug=slug))

        flash('Invalid username or password.', 'danger')

    return render_template('pos/login.html', business=biz)


@pos_bp.route('/<slug>/pos/logout')
def pos_logout(slug):
    biz = _load_biz(slug)
    if current_user.is_authenticated and current_user.role == 'sales_rep':
        ws = WorkSession.query.filter_by(user_id=current_user.id, is_active=True).first()
        if ws:
            ws.clock_out = datetime.utcnow()
            ws.is_active = False
            db.session.commit()
        flash('You have been clocked out. Goodbye!', 'info')
    else:
        flash('You have been logged out.', 'info')
    logout_user()
    return redirect(url_for('pos.pos_login', slug=slug))


# ─── POS Interface ────────────────────────────────────────────────────────────

@pos_bp.route('/<slug>/pos/')
def pos_home(slug):
    biz = _load_biz(slug)
    if not current_user.is_authenticated or current_user.business_id != biz.id:
        return redirect(url_for('pos.pos_login', slug=slug))

    cats = Category.query.filter_by(business_id=biz.id).all()
    cat_id = request.args.get('cat', type=int)
    q = request.args.get('q', '').strip()

    query = Product.query.filter_by(business_id=biz.id, is_active=True)
    if cat_id:
        query = query.filter_by(category_id=cat_id)
    if q:
        query = query.filter(Product.name.ilike(f'%{q}%'))
    prods = query.order_by(Product.name).all()

    active_session = None
    if current_user.role == 'sales_rep':
        active_session = WorkSession.query.filter_by(
            user_id=current_user.id, is_active=True
        ).first()

    return render_template('pos/pos.html', business=biz, products=prods,
                           categories=cats, selected_cat=cat_id, q=q,
                           active_session=active_session)


# ─── Barcode Lookup (AJAX) ───────────────────────────────────────────────────

@pos_bp.route('/<slug>/pos/barcode')
def barcode_lookup(slug):
    biz = _load_biz(slug)
    if not current_user.is_authenticated or current_user.business_id != biz.id:
        return jsonify({'error': 'unauthorized'}), 401
    code = request.args.get('code', '').strip()
    if not code:
        return jsonify({'product': None, 'error': 'No barcode provided'}), 400
    prod = Product.query.filter_by(business_id=biz.id, barcode=code, is_active=True).first()
    if not prod:
        return jsonify({'product': None, 'error': f'No product found for barcode: {code}'}), 404
    return jsonify({'product': {
        'id': prod.id,
        'name': prod.name,
        'barcode': prod.barcode,
        'unit_price': float(prod.unit_price),
        'quantity_in_stock': prod.quantity_in_stock,
        'unit': prod.unit,
    }})


# ─── Product Search (AJAX) ────────────────────────────────────────────────────

@pos_bp.route('/<slug>/pos/products')
def pos_products(slug):
    biz = _load_biz(slug)
    if not current_user.is_authenticated or current_user.business_id != biz.id:
        return jsonify({'error': 'unauthorized'}), 401

    q = request.args.get('q', '').strip()
    cat_id = request.args.get('cat', type=int)
    query = Product.query.filter_by(business_id=biz.id, is_active=True)
    if cat_id:
        query = query.filter_by(category_id=cat_id)
    if q:
        query = query.filter(Product.name.ilike(f'%{q}%'))
    prods = query.order_by(Product.name).limit(50).all()

    return jsonify([{
        'id': p.id,
        'name': p.name,
        'unit_price': float(p.unit_price),
        'quantity_in_stock': p.quantity_in_stock,
        'unit': p.unit,
    } for p in prods])


# ─── Process Sale (AJAX) ──────────────────────────────────────────────────────

@pos_bp.route('/<slug>/pos/checkout', methods=['POST'])
def checkout(slug):
    biz = _load_biz(slug)
    if not current_user.is_authenticated or current_user.business_id != biz.id:
        return jsonify({'error': 'unauthorized'}), 401

    data = request.get_json()
    if not data or not data.get('items'):
        return jsonify({'error': 'No items in cart'}), 400

    items = data['items']
    payment_method = data.get('payment_method', 'cash')
    amount_tendered = Decimal(str(data.get('amount_tendered', 0)))
    customer_name = data.get('customer_name', '').strip()
    discount = Decimal(str(data.get('discount', 0)))

    if discount < 0:
        discount = Decimal('0')

    subtotal = Decimal('0')
    sale_items = []

    for item in items:
        prod = Product.query.filter_by(id=item['product_id'], business_id=biz.id, is_active=True).first()
        if not prod:
            return jsonify({'error': f'Product not found: {item["product_id"]}'}), 400
        qty = int(item['quantity'])
        if qty <= 0:
            return jsonify({'error': f'Invalid quantity for {prod.name}'}), 400
        if prod.quantity_in_stock < qty:
            return jsonify({'error': f'Insufficient stock for {prod.name}. Available: {prod.quantity_in_stock}'}), 400

        unit_price = prod.unit_price
        item_subtotal = unit_price * qty
        subtotal += item_subtotal
        sale_items.append((prod, qty, unit_price, item_subtotal))

    total = subtotal - discount
    if total < 0:
        total = Decimal('0')

    change = amount_tendered - total if payment_method == 'cash' else Decimal('0')
    if change < 0:
        if payment_method == 'cash':
            return jsonify({'error': 'Amount tendered is less than total.'}), 400
        change = Decimal('0')

    # Get active work session for sales rep
    active_session_id = None
    if current_user.role == 'sales_rep':
        ws = WorkSession.query.filter_by(user_id=current_user.id, is_active=True).first()
        if ws:
            active_session_id = ws.id

    sale_number = generate_sale_number(biz)
    sale = Sale(
        business_id=biz.id,
        user_id=current_user.id,
        session_id=active_session_id,
        sale_number=sale_number,
        subtotal=subtotal,
        discount=discount,
        total_amount=total,
        amount_tendered=amount_tendered,
        change_given=change,
        payment_method=payment_method,
        customer_name=customer_name,
    )
    db.session.add(sale)
    db.session.flush()

    for prod, qty, unit_price, item_subtotal in sale_items:
        si = SaleItem(
            sale_id=sale.id,
            product_id=prod.id,
            product_name=prod.name,
            quantity=qty,
            unit_price=unit_price,
            subtotal=item_subtotal,
        )
        db.session.add(si)
        prod.quantity_in_stock -= qty

    db.session.commit()

    return jsonify({
        'success': True,
        'sale_number': sale_number,
        'sale_id': sale.id,
        'total': float(total),
        'change': float(change),
        'receipt_url': url_for('pos.receipt', slug=slug, sale_id=sale.id),
    })


# ─── Receipt ─────────────────────────────────────────────────────────────────

@pos_bp.route('/<slug>/pos/receipt/<int:sale_id>')
def receipt(slug, sale_id):
    biz = _load_biz(slug)
    if not current_user.is_authenticated or current_user.business_id != biz.id:
        return redirect(url_for('pos.pos_login', slug=slug))
    sale = Sale.query.filter_by(id=sale_id, business_id=biz.id).first_or_404()
    return render_template('pos/receipt.html', business=biz, sale=sale)


# ─── My Sessions (Sales Rep) ──────────────────────────────────────────────────

@pos_bp.route('/<slug>/pos/my-sessions')
def my_sessions(slug):
    biz = _load_biz(slug)
    if not current_user.is_authenticated or current_user.business_id != biz.id:
        return redirect(url_for('pos.pos_login', slug=slug))
    sessions = (WorkSession.query
                .filter_by(user_id=current_user.id, business_id=biz.id)
                .order_by(WorkSession.clock_in.desc())
                .limit(20).all())
    return render_template('pos/sessions.html', business=biz, sessions=sessions)
