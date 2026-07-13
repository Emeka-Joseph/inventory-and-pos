from datetime import datetime, timedelta, date as date_type
from decimal import Decimal, InvalidOperation
from io import BytesIO
import openpyxl
from openpyxl.styles import Font
from flask import (Blueprint, render_template, redirect, url_for, request, flash, g,
                    jsonify, abort)
from flask_login import login_required, login_user, current_user
from ..extensions import db
from ..models import Product, Category, User, Business, WarehouseEntry, WarehouseMovement
from ..config import PLAN_LIMITS
from ..utils import load_business_or_404, send_excel_file

warehouse_bp = Blueprint('warehouse', __name__)

# A completely separate stock pool from the store/shop-floor (Product.quantity_in_stock).
# New products start with zero warehouse stock. Stock is added here in bulk when the
# business owner buys in quantity, and "moved out" is just a quantity/date/person log —
# any resulting store restock is a separate, ordinary store stock update.
#
# Accessible to admins and warehouse keepers (role='store_keeper') only — this is the
# entire portal a warehouse keeper sees; every other admin area stays off-limits to them.


def _load_biz(slug):
    biz = load_business_or_404(slug)
    if not current_user.is_authenticated or current_user.business_id != biz.id:
        abort(403)
    if current_user.role not in ('admin', 'store_keeper'):
        abort(403)
    g.business = biz
    return biz


# ─── Warehouse Keeper Login ───────────────────────────────────────────────────
# Exclusive to warehouse keepers (role='store_keeper') — no cross-links to the
# admin or POS logins, since each role's login is a distinct, self-contained portal.

@warehouse_bp.route('/<slug>/warehouse/login', methods=['GET', 'POST'])
def warehouse_login(slug):
    biz = Business.query.filter_by(slug=slug).first_or_404()

    if biz.status == 'pending':
        return render_template('auth/business_status.html', business=biz, status='pending')
    if biz.status == 'suspended':
        return render_template('auth/business_status.html', business=biz, status='suspended')

    if current_user.is_authenticated and current_user.business_id == biz.id:
        if current_user.role == 'store_keeper':
            return redirect(url_for('warehouse.warehouse', slug=slug))
        if current_user.role == 'admin':
            return redirect(url_for('admin.dashboard', slug=slug))
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
            if user.role != 'store_keeper':
                flash('This login is for Warehouse Keepers only.', 'warning')
                return render_template('warehouse/login.html', business=biz)
            login_user(user)
            flash(f'Welcome back, {user.full_name}!', 'success')
            return redirect(url_for('warehouse.warehouse', slug=slug))

        flash('Invalid username or password.', 'danger')

    return render_template('warehouse/login.html', business=biz)


@warehouse_bp.route('/<slug>/warehouse/')
@login_required
def warehouse(slug):
    biz = _load_biz(slug)
    cats = Category.query.filter_by(business_id=biz.id).all()
    cat_id = request.args.get('cat', type=int)
    q = request.args.get('q', '').strip()
    exp_filter = request.args.get('exp', '').strip()

    query = Product.query.filter_by(business_id=biz.id, is_active=True)
    if cat_id:
        query = query.filter_by(category_id=cat_id)
    if q:
        query = query.filter(Product.name.ilike(f'%{q}%'))
    if exp_filter == 'expired':
        query = query.filter(Product.expiry_date.isnot(None), Product.expiry_date < date_type.today())
    elif exp_filter == 'expiring_soon':
        today = date_type.today()
        query = query.filter(Product.expiry_date.isnot(None),
                             Product.expiry_date >= today,
                             Product.expiry_date <= today + timedelta(days=30))
    prods = query.order_by(Product.name).all()

    total_units = sum(p.quantity_in_warehouse or 0 for p in prods)
    total_value = sum((p.warehouse_stock_value for p in prods), Decimal('0'))
    stocked_count = sum(1 for p in prods if (p.quantity_in_warehouse or 0) > 0)

    all_products = Product.query.filter_by(business_id=biz.id, is_active=True).order_by(Product.name).all()

    entries = (WarehouseEntry.query.filter_by(business_id=biz.id)
              .order_by(WarehouseEntry.created_at.desc()).limit(50).all())
    movements = (WarehouseMovement.query.filter_by(business_id=biz.id)
                .order_by(WarehouseMovement.created_at.desc()).limit(50).all())

    activity = []
    for e in entries:
        activity.append({'type': 'in', 'sort_at': e.created_at, 'display_date': e.created_at.date(),
                         'product': e.product.name, 'quantity': e.quantity,
                         'person': e.recorder.full_name, 'notes': e.notes})
    for m in movements:
        activity.append({'type': 'out', 'sort_at': m.created_at, 'display_date': m.movement_date,
                         'product': m.product.name, 'quantity': m.quantity,
                         'person': m.taken_by, 'notes': m.notes})
    activity.sort(key=lambda a: a['sort_at'], reverse=True)
    activity = activity[:30]

    return render_template('warehouse/warehouse.html', business=biz, products=prods,
                           categories=cats, selected_cat=cat_id, q=q, exp_filter=exp_filter,
                           total_units=total_units, total_value=total_value,
                           stocked_count=stocked_count, all_products=all_products,
                           activity=activity)


@warehouse_bp.route('/<slug>/warehouse/check-barcode')
@login_required
def check_barcode(slug):
    """Live duplicate-barcode lookup used by the new-stock form as you scan/type."""
    biz = _load_biz(slug)
    barcode = request.args.get('barcode', '').strip()
    if not barcode:
        return jsonify(exists=False)
    existing = Product.query.filter_by(business_id=biz.id, barcode=barcode).first()
    return jsonify(exists=existing is not None, product_name=existing.name if existing else None)


@warehouse_bp.route('/<slug>/warehouse/new-product', methods=['GET', 'POST'])
@login_required
def new_product(slug):
    """Register a brand-new product straight into the warehouse — for stock never purchased before.
    Store stock (quantity_in_stock) starts at 0, same as any other new product."""
    biz = _load_biz(slug)
    sub = biz.subscription
    features = sub.features if sub else PLAN_LIMITS['free']
    max_products = features.get('max_products')
    if max_products is not None:
        current_count = Product.query.filter_by(business_id=biz.id, is_active=True).count()
        if current_count >= max_products:
            flash(f'This business plan allows up to {max_products} products. '
                  f'Contact your admin to upgrade.', 'warning')
            return redirect(url_for('warehouse.warehouse', slug=slug))

    cats = Category.query.filter_by(business_id=biz.id).all()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Product name is required.', 'danger')
            return render_template('warehouse/new_product.html', business=biz, categories=cats)

        barcode = request.form.get('barcode', '').strip()
        if not barcode:
            flash('Barcode is required.', 'danger')
            return render_template('warehouse/new_product.html', business=biz, categories=cats)
        if Product.query.filter_by(business_id=biz.id, barcode=barcode).first():
            flash(f'Barcode "{barcode}" is already assigned to another product.', 'danger')
            return render_template('warehouse/new_product.html', business=biz, categories=cats)

        mfg_raw = request.form.get('manufacture_date', '').strip()
        exp_raw = request.form.get('expiry_date', '').strip()
        try:
            mfg_date = datetime.strptime(mfg_raw, '%d/%m/%Y').date() if mfg_raw else None
            exp_date = datetime.strptime(exp_raw, '%d/%m/%Y').date() if exp_raw else None
        except ValueError:
            flash('Invalid date format. Please use dd/mm/yyyy.', 'danger')
            return render_template('warehouse/new_product.html', business=biz, categories=cats)

        initial_qty = int(request.form.get('initial_qty', 0) or 0)
        cost_price = Decimal(request.form.get('cost_price', '0') or '0')

        prod = Product(
            business_id=biz.id,
            name=name,
            barcode=barcode,
            manufacture_date=mfg_date,
            expiry_date=exp_date,
            description=request.form.get('description', '').strip(),
            unit_price=Decimal(request.form.get('unit_price', '0') or '0'),
            cost_price=cost_price,
            reorder_level=int(request.form.get('reorder_level', 5) or 5),
            unit=request.form.get('unit', 'piece').strip(),
            category_id=request.form.get('category_id', type=int) or None,
            quantity_in_warehouse=initial_qty,
        )
        db.session.add(prod)
        db.session.flush()

        if initial_qty > 0:
            db.session.add(WarehouseEntry(
                business_id=biz.id,
                product_id=prod.id,
                user_id=current_user.id,
                quantity=initial_qty,
                unit_cost=cost_price,
                total_cost=cost_price * initial_qty,
                notes='Initial stock on product creation',
            ))

        db.session.commit()
        flash(f'Product "{name}" created with {initial_qty} unit(s) in the warehouse.', 'success')
        return redirect(url_for('warehouse.warehouse', slug=slug))

    return render_template('warehouse/new_product.html', business=biz, categories=cats)


@warehouse_bp.route('/<slug>/warehouse/add-stock', methods=['POST'])
@login_required
def add_stock(slug):
    """Bulk stock purchase received into the warehouse."""
    biz = _load_biz(slug)
    product_id = request.form.get('product_id', type=int)
    quantity = request.form.get('quantity', type=int)
    unit_cost_raw = request.form.get('unit_cost', '').strip()
    notes = request.form.get('notes', '').strip()

    prod = Product.query.filter_by(id=product_id, business_id=biz.id).first_or_404()

    if not quantity or quantity <= 0:
        flash('Enter a valid quantity greater than 0.', 'danger')
        return redirect(url_for('warehouse.warehouse', slug=slug))

    try:
        unit_cost = Decimal(unit_cost_raw) if unit_cost_raw else (prod.cost_price or Decimal('0'))
    except InvalidOperation:
        flash('Enter a valid unit cost.', 'danger')
        return redirect(url_for('warehouse.warehouse', slug=slug))

    prod.quantity_in_warehouse = (prod.quantity_in_warehouse or 0) + quantity
    db.session.add(WarehouseEntry(
        business_id=biz.id,
        product_id=prod.id,
        user_id=current_user.id,
        quantity=quantity,
        unit_cost=unit_cost,
        total_cost=unit_cost * quantity,
        notes=notes,
    ))
    db.session.commit()
    flash(f'Added {quantity} unit(s) of "{prod.name}" to warehouse stock.', 'success')
    return redirect(url_for('warehouse.warehouse', slug=slug))


@warehouse_bp.route('/<slug>/warehouse/record-movement', methods=['POST'])
@login_required
def record_movement(slug):
    """Log stock taken out of the warehouse — quantity, date, and who took it. No destination is tracked;
    if it's headed to the store, update store stock separately as usual."""
    biz = _load_biz(slug)
    product_id = request.form.get('product_id', type=int)
    quantity = request.form.get('quantity', type=int)
    taken_by = request.form.get('taken_by', '').strip()
    movement_date_raw = request.form.get('movement_date', '').strip()
    notes = request.form.get('notes', '').strip()

    prod = Product.query.filter_by(id=product_id, business_id=biz.id).first_or_404()

    if not quantity or quantity <= 0:
        flash('Enter a valid quantity greater than 0.', 'danger')
        return redirect(url_for('warehouse.warehouse', slug=slug))
    if not taken_by:
        flash('Enter the name of the person taking the stock.', 'danger')
        return redirect(url_for('warehouse.warehouse', slug=slug))
    if quantity > (prod.quantity_in_warehouse or 0):
        flash(f'Only {prod.quantity_in_warehouse or 0} unit(s) of "{prod.name}" are in the warehouse.', 'danger')
        return redirect(url_for('warehouse.warehouse', slug=slug))

    try:
        movement_date = (datetime.strptime(movement_date_raw, '%d/%m/%Y').date()
                         if movement_date_raw else date_type.today())
    except ValueError:
        flash('Invalid date format. Please use dd/mm/yyyy.', 'danger')
        return redirect(url_for('warehouse.warehouse', slug=slug))

    prod.quantity_in_warehouse -= quantity
    db.session.add(WarehouseMovement(
        business_id=biz.id,
        product_id=prod.id,
        quantity=quantity,
        taken_by=taken_by,
        movement_date=movement_date,
        recorded_by=current_user.id,
        notes=notes,
    ))
    db.session.commit()
    flash(f'Recorded {quantity} unit(s) of "{prod.name}" taken out of the warehouse by {taken_by}.', 'success')
    return redirect(url_for('warehouse.warehouse', slug=slug))


@warehouse_bp.route('/<slug>/warehouse/export')
@login_required
def export_warehouse(slug):
    """Export the warehouse-only inventory to an .xlsx file."""
    biz = _load_biz(slug)
    prods = (Product.query.filter_by(business_id=biz.id, is_active=True)
            .order_by(Product.name).all())

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Warehouse'
    headers = ['Name', 'Barcode', 'Category', 'Quantity In Warehouse', 'Unit Cost', 'Warehouse Value']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for p in prods:
        ws.append([
            p.name, p.barcode, p.category.name if p.category else '',
            p.quantity_in_warehouse or 0, float(p.cost_price or 0), float(p.warehouse_stock_value),
        ])

    for col_cells in ws.columns:
        length = max((len(str(c.value)) if c.value is not None else 0) for c in col_cells)
        ws.column_dimensions[col_cells[0].column_letter].width = max(12, length + 2)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"{biz.slug}-warehouse-{datetime.utcnow().strftime('%Y%m%d')}.xlsx"
    return send_excel_file(buf, filename)
