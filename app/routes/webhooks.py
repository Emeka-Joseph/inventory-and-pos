import hashlib
import hmac
from datetime import datetime, timedelta
from flask import Blueprint, request, abort, current_app
from ..extensions import db
from ..models import Business, Subscription

webhooks_bp = Blueprint('webhooks', __name__)


@webhooks_bp.route('/webhooks/paystack', methods=['POST'])
def paystack():
    """Paystack server-to-server event notification."""
    secret = current_app.config.get('PAYSTACK_SECRET_KEY', '').encode('utf-8')
    sig    = request.headers.get('X-Paystack-Signature', '')
    computed = hmac.new(secret, request.data, hashlib.sha512).hexdigest()

    if not hmac.compare_digest(computed, sig):
        abort(400)

    payload = request.get_json(force=True) or {}
    event   = payload.get('event')

    if event == 'charge.success':
        _handle_charge_success(payload.get('data', {}))

    return '', 200


def _handle_charge_success(data):
    meta   = data.get('metadata', {})
    biz_id = meta.get('business_id')
    plan   = meta.get('plan')
    cycle  = meta.get('billing_cycle', 'monthly')

    if not biz_id or plan not in ('pro', 'premium'):
        return

    biz = Business.query.get(biz_id)
    if not biz:
        return

    _activate_plan(biz, plan, cycle,
                   customer_code=data.get('customer', {}).get('customer_code'),
                   reference=data.get('reference'))


def _activate_plan(biz, plan, cycle, customer_code=None, reference=None):
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
    if customer_code:
        sub.paystack_customer_code = customer_code
    if reference:
        sub.paystack_reference = reference

    db.session.commit()
