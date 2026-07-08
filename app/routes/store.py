from datetime import datetime, date as date_type
from decimal import Decimal
from flask import Blueprint, render_template, redirect, url_for, request, flash, g, abort, jsonify
from flask_login import login_required, current_user
from ..extensions import db
from ..models import Product, Category, StockEntry, User
from ..utils import load_business_or_404, get_date_range

store_bp = Blueprint('store', __name__)


def _load_biz(slug):
    biz = load_business_or_404(slug)
    if not current_user.is_authenticated or current_user.business_id != biz.id:
        abort(403)
    if current_user.role != 'admin':
        abort(403)
    g.business = biz
    return biz


@store_bp.route('/<slug>/store/')
@login_required
def dashboard(slug):
    biz = _load_biz(slug)
    total_products = Product.query.filter_by(business_id=biz.id, is_active=True).count()
    low_stock = Product.query.filter(
        Product.business_id == biz.id,
        Product.is_active == True,
        Product.quantity_in_stock <= Product.reorder_level
    ).all()
    recent_entries = (StockEntry.query
                      .filter_by(business_id=biz.id)
                      .order_by(StockEntry.created_at.desc())
                      .limit(15).all())
    return render_template('store/dashboard.html', business=biz,
                           total_products=total_products,
                           low_stock=low_stock,
                           recent_entries=recent_entries)


@store_bp.route('/<slug>/store/products')
@login_required
def products(slug):
    biz = _load_biz(slug)
    cats = Category.query.filter_by(business_id=biz.id).all()
    cat_id = request.args.get('cat', type=int)
    q = request.args.get('q', '').strip()
    query = Product.query.filter_by(business_id=biz.id, is_active=True)
    if cat_id:
        query = query.filter_by(category_id=cat_id)
    if q:
        query = query.filter(Product.name.ilike(f'%{q}%'))
    prods = query.order_by(Product.name).all()
    return render_template('store/products.html', business=biz, products=prods,
                           categories=cats, selected_cat=cat_id, q=q)


@store_bp.route('/<slug>/store/products/check-barcode')
@login_required
def check_barcode(slug):
    """Live duplicate-barcode lookup used by the add product form as you scan/type."""
    biz = _load_biz(slug)
    barcode = request.args.get('barcode', '').strip()
    if not barcode:
        return jsonify(exists=False)
    existing = Product.query.filter_by(business_id=biz.id, barcode=barcode).first()
    return jsonify(exists=existing is not None, product_name=existing.name if existing else None)


@store_bp.route('/<slug>/store/products/new', methods=['GET', 'POST'])
@login_required
def add_product(slug):
    biz = _load_biz(slug)
    cats = Category.query.filter_by(business_id=biz.id).all()

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Product name is required.', 'danger')
            return render_template('store/add_product.html', business=biz, categories=cats)

        existing = Product.query.filter(
            Product.business_id == biz.id,
            Product.name.ilike(name)
        ).first()
        if existing:
            flash(f'A product named "{existing.name}" already exists. Use stock entry to add stock.', 'warning')
            return redirect(url_for('store.stock_entry', slug=slug))

        barcode = request.form.get('barcode', '').strip()
        if not barcode:
            flash('Barcode is required.', 'danger')
            return render_template('store/add_product.html', business=biz, categories=cats)
        if Product.query.filter_by(business_id=biz.id, barcode=barcode).first():
            flash(f'Barcode "{barcode}" is already assigned to another product. Use stock entry to add stock.', 'warning')
            return redirect(url_for('store.stock_entry', slug=slug))

        initial_qty = int(request.form.get('initial_qty', 0) or 0)
        unit_price = Decimal(request.form.get('unit_price', '0') or '0')
        cost_price = Decimal(request.form.get('cost_price', '0') or '0')

        mfg_raw = request.form.get('manufacture_date', '').strip()
        exp_raw = request.form.get('expiry_date', '').strip()
        try:
            mfg_date = datetime.strptime(mfg_raw, '%d/%m/%Y').date() if mfg_raw else None
            exp_date = datetime.strptime(exp_raw, '%d/%m/%Y').date() if exp_raw else None
        except ValueError:
            flash('Invalid date format. Please use dd/mm/yyyy.', 'danger')
            return render_template('store/add_product.html', business=biz, categories=cats)

        prod = Product(
            business_id=biz.id,
            name=name,
            barcode=barcode,
            manufacture_date=mfg_date,
            expiry_date=exp_date,
            description=request.form.get('description', '').strip(),
            unit_price=unit_price,
            cost_price=cost_price,
            quantity_in_stock=initial_qty,
            reorder_level=int(request.form.get('reorder_level', 5) or 5),
            unit=request.form.get('unit', 'piece').strip(),
            category_id=request.form.get('category_id', type=int) or None,
        )
        db.session.add(prod)
        db.session.flush()

        if initial_qty > 0:
            entry = StockEntry(
                business_id=biz.id,
                product_id=prod.id,
                user_id=current_user.id,
                quantity=initial_qty,
                unit_cost=cost_price,
                total_cost=cost_price * initial_qty,
                notes='Initial stock entry on product creation',
            )
            db.session.add(entry)

        db.session.commit()
        flash(f'Product "{name}" added with {initial_qty} units.', 'success')
        return redirect(url_for('store.products', slug=slug))

    return render_template('store/add_product.html', business=biz, categories=cats)


@store_bp.route('/<slug>/store/stock-entry', methods=['GET', 'POST'])
@login_required
def stock_entry(slug):
    biz = _load_biz(slug)
    prods = Product.query.filter_by(business_id=biz.id, is_active=True).order_by(Product.name).all()

    if request.method == 'POST':
        product_id = request.form.get('product_id', type=int)
        quantity = request.form.get('quantity', type=int)
        unit_cost = Decimal(request.form.get('unit_cost', '0') or '0')
        reference = request.form.get('reference', '').strip()
        notes = request.form.get('notes', '').strip()

        errors = []
        if not product_id:
            errors.append('Please select a product.')
        if not quantity or quantity <= 0:
            errors.append('Quantity must be greater than zero.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('store/stock_entry.html', business=biz, products=prods)

        prod = Product.query.filter_by(id=product_id, business_id=biz.id).first_or_404()

        entry = StockEntry(
            business_id=biz.id,
            product_id=prod.id,
            user_id=current_user.id,
            quantity=quantity,
            unit_cost=unit_cost,
            total_cost=unit_cost * quantity,
            reference=reference,
            notes=notes,
        )
        db.session.add(entry)
        prod.quantity_in_stock += quantity
        db.session.commit()

        flash(f'Stock entry recorded: {quantity} x {prod.name}.', 'success')
        return redirect(url_for('store.stock_entry', slug=slug))

    entries = (StockEntry.query
               .filter_by(business_id=biz.id)
               .order_by(StockEntry.created_at.desc())
               .limit(30).all())
    return render_template('store/stock_entry.html', business=biz, products=prods, entries=entries)


@store_bp.route('/<slug>/store/categories/new', methods=['POST'])
@login_required
def new_category(slug):
    biz = _load_biz(slug)
    name = request.form.get('name', '').strip()
    if name:
        cat = Category(business_id=biz.id, name=name)
        db.session.add(cat)
        db.session.commit()
        flash(f'Category "{name}" added.', 'success')
    return redirect(request.referrer or url_for('store.products', slug=slug))
