import os
import traceback
from flask import Flask, render_template
from .config import Config
from .extensions import db, login_manager, migrate, csrf, mail


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    mail.init_app(app)

    login_manager.login_view = 'auth.login_redirect'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'warning'

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    @app.context_processor
    def inject_globals():
        from flask_login import current_user
        current_sub   = None
        plan_features = None
        if current_user.is_authenticated:
            from .models import Subscription
            from .config import PLAN_LIMITS
            sub = Subscription.query.filter_by(
                business_id=current_user.business_id
            ).first()
            if sub:
                current_sub   = sub
                plan_features = sub.features
            else:
                plan_features = PLAN_LIMITS['free']
        return {
            'CURRENCIES':    config_class.CURRENCIES,
            'current_sub':   current_sub,
            'plan_features': plan_features,
        }

    from .routes.main import main_bp
    from .routes.auth import auth_bp
    from .routes.admin import admin_bp
    from .routes.store import store_bp
    from .routes.warehouse import warehouse_bp
    from .routes.pos import pos_bp
    from .routes.superadmin import superadmin_bp
    from .routes.webhooks import webhooks_bp
    from .routes.qz import qz_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(store_bp)
    app.register_blueprint(warehouse_bp)
    app.register_blueprint(pos_bp)
    app.register_blueprint(superadmin_bp, url_prefix='/superadmin')
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(qz_bp)

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def internal_error(e):
        # A failed request can leave the DB session in a broken state for the
        # next request on this same worker — always roll back.
        db.session.rollback()
        app.logger.error('Internal Server Error', exc_info=True)

        show_details = app.config.get('SHOW_DEBUG_ERRORS', False)
        tb = traceback.format_exc() if show_details else None
        return render_template('errors/500.html', traceback=tb), 500

    # Start reorder-alert scheduler (once, even in debug mode)
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        _start_scheduler(app)

    return app


def _start_scheduler(app):
    from apscheduler.schedulers.background import BackgroundScheduler
    from .utils import check_reorder_alerts, check_subscriptions

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        func=lambda: check_reorder_alerts(app),
        trigger='interval',
        hours=1,
        id='reorder_alerts',
        replace_existing=True,
    )
    scheduler.add_job(
        func=lambda: check_subscriptions(app),
        trigger='interval',
        hours=1,
        id='subscription_checks',
        replace_existing=True,
    )
    scheduler.start()
