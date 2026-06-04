"""
Administrator Routes Blueprint
Contains customer management, billing management, overdue bill handling,
organization settings, team management, service/parts catalog, and inventory
"""
from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, session, g
from datetime import date, timedelta
import logging
from app.services.customer_service import CustomerService
from app.services.job_service import JobService
from app.services.billing_service import BillingService
from app.utils.decorators import handle_database_errors, log_function_call, validate_pagination
from app.utils.validators import sanitize_input, validate_positive_integer, validate_service_data, validate_part_data
from app.models.service import Service
from app.models.part import Part
from datetime import date

# Create blueprint
administrator_bp = Blueprint('administrator', __name__)
logger = logging.getLogger(__name__)

# Initialize services
customer_service = CustomerService()
job_service = JobService()
billing_service = BillingService()


def require_admin_login():
    """Check administrator login status"""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('auth.login'))

    if session.get('current_role') not in ('owner', 'admin'):
        flash('Administrator privileges required', 'error')
        return redirect(url_for('main.index'))

    return None


@administrator_bp.route('/dashboard')
@handle_database_errors
@log_function_call
def dashboard():
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        page = request.args.get('page', 1, type=int)
        per_page = 10

        job_stats = job_service.get_job_statistics()
        billing_stats = billing_service.get_billing_statistics()
        total_customers = len(customer_service.get_all_customers())
        customers_with_unpaid = customer_service.get_customers_with_filter(has_unpaid=True)
        customers_with_overdue = customer_service.get_customers_with_filter(has_overdue=True)

        recent_jobs, total_jobs, total_pages = job_service.get_unpaid_and_pending_jobs(page=page, per_page=per_page, with_assignee=True)
        overdue_bills = billing_service.get_overdue_bills()[:5]
        
        return render_template('administrator/dashboard.html',
                               job_stats=job_stats,
                               billing_stats=billing_stats,
                               total_customers=total_customers,
                               customers_with_unpaid=len(customers_with_unpaid),
                               customers_with_overdue=len(customers_with_overdue),
                               recent_jobs=recent_jobs,
                               overdue_bills=overdue_bills,
                               current_date=date.today(),
                               page=page,
                               per_page=per_page,
                               total_jobs=total_jobs,
                               total_pages=total_pages)

    except Exception as e:
        logger.error(f"Administrator dashboard loading failed: {e}")
        flash('Failed to load dashboard', 'error')
        return render_template('administrator/dashboard.html',
                               job_stats={}, billing_stats={},
                               total_customers=0, customers_with_unpaid=0,
                               customers_with_overdue=0, recent_jobs=[],
                               overdue_bills=[], current_date=date.today(),
                               page=1, per_page=5, total_jobs=0, total_pages=0)


@administrator_bp.route('/dashboard/revenue')
@handle_database_errors
def revenue_by_year():
    year = request.args.get('year', date.today().year, type=int)
    monthly_revenue = billing_service.get_monthly_revenue(year)
    return jsonify({'monthly_revenue': monthly_revenue})


@administrator_bp.route('/jobs/<int:job_id>/invoice')
@handle_database_errors
def job_invoice(job_id):
    """Invoice page for a job"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        job = job_service.get_job_by_id(job_id)

        if not job:
            flash('Job not found', 'error')
            return redirect(url_for('technician.current_jobs'))

        services_total = sum(float(js.total_cost) for js in job.job_services)
        parts_total = sum(float(jp.total_cost) for jp in job.job_parts)
        due_date = job.job_date + timedelta(days=30)

        return render_template('administrator/invoice.html',
                            job=job,
                            services_total=services_total,
                            parts_total=parts_total,
                            due_date=due_date,
                            invoice_number=f'INV-{job.job_id:05d}')
        
    except Exception as e:
        logger.error(f"Invoice generation failed: {e}")
        flash('Failed to generate invoice', 'error')
        return render_template('administrator/invoice.html',
                            job={},
                            services_total=0,
                            parts_total=0,
                            due_date=None,
                            invoice_number=f'INV-00000')


@administrator_bp.route('/customers')
@validate_pagination
@handle_database_errors
@log_function_call
def customer_list(page=1, per_page=20):
    """Customer management page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        import traceback
        # Get filter parameters
        filter_type = sanitize_input(request.args.get('filter', 'all'))
        search_query = sanitize_input(request.args.get('search', ''))

        # Get customers based on filter
        if filter_type == 'unpaid':
            customers = customer_service.get_customers_with_filter(has_unpaid=True)
        elif filter_type == 'overdue':
            customers = customer_service.get_customers_with_filter(has_overdue=True)
        elif search_query:
            customers_obj = customer_service.search_customers(search_query)
            customers = [c.to_dict() for c in customers_obj]
            # Add statistics info
            for customer in customers:
                customer['total_unpaid'] = customer_service.get_customer_by_id(customer['customer_id']).get_total_unpaid_amount()
                customer['has_overdue'] = customer_service.get_customer_by_id(customer['customer_id']).has_overdue_bills()
        else:
            customers_obj = customer_service.get_all_customers()
            customers = []
            for c in customers_obj:
                customer_data = c.to_dict()
                customer_data['total_unpaid'] = c.get_total_unpaid_amount()
                customer_data['has_overdue'] = c.has_overdue_bills()
                customers.append(customer_data)

        # Simple pagination
        total = len(customers)
        start = (page - 1) * per_page
        end = start + per_page
        customers_page = customers[start:end]
        total_pages = (total + per_page - 1) // per_page
        
        job_page = request.args.get('job_page', 1, type=int)
        job_per_page = 10
        print("DEBUG job_page:", job_page)
        recent_jobs, total_jobs, total_job_pages = job_service.get_unpaid_and_pending_jobs(job_page, job_per_page)
        print("DEBUG recent_jobs:", len(recent_jobs), "total:", total_jobs, "pages:", total_job_pages)

        return render_template('administrator/customer_list.html',
                             customers=customers_page,
                             page=page,
                             per_page=per_page,
                             total=total,
                             total_pages=total_pages,
                             total_jobs=total_jobs,
                             total_job_pages=total_job_pages,
                             job_page=job_page, 
                             filter_type=filter_type,
                             search_query=search_query,
                             jobs=recent_jobs,
                             services=Service.get_active_sorted(),
                             parts=Part.get_active_sorted(),
                             today=date.today().isoformat())

    except Exception as e:
        logger.error(f"Customer management page loading failed: {e}")
        traceback.print_exc()
        flash('Failed to load customer list', 'error')
        return render_template('administrator/customer_list.html',
                           customers=[],
                           page=1,
                           per_page=per_page,
                           total=0,
                           total_pages=0,
                           filter_type='all',
                           search_query='',
                           jobs=[],
                           total_jobs=0,
                           total_job_pages=0,
                           job_page=1,
                           services=[],
                           parts=[],
                           today=date.today().isoformat())


@administrator_bp.route('/billing')
@handle_database_errors
@log_function_call
def billing_management():
    """Billing management page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        # Get filter parameters
        filter_type = sanitize_input(request.args.get('filter', 'unpaid'))
        customer_name = sanitize_input(request.args.get('customer', ''))

        # Get billing data
        if filter_type == 'overdue':
            bills = billing_service.get_overdue_bills()
        elif filter_type == 'all':
            bills = billing_service.get_all_bills_with_status()
        else:  # unpaid
            bills = billing_service.get_unpaid_bills(customer_name if customer_name != 'Choose...' else None)

        # Get customer name list for dropdown
        customers = customer_service.get_all_customers()
        customer_names = [f"{c.first_name} {c.family_name}".strip() for c in customers]
        customer_names = list(set(customer_names))  # Remove duplicates
        customer_names.sort()

        # Get billing statistics
        billing_stats = billing_service.get_billing_statistics()

        return render_template('administrator/billing.html',
                             bills=bills,
                             filter_type=filter_type,
                             customer_name=customer_name,
                             customer_names=customer_names,
                             billing_stats=billing_stats)

    except Exception as e:
        logger.error(f"Billing management page loading failed: {e}")
        flash('Failed to load billing management page', 'error')
        return render_template('administrator/billing.html',
                             bills=[],
                             filter_type='unpaid',
                             customer_name='',
                             customer_names=[],
                             billing_stats={})


@administrator_bp.route('/bill-history', methods=['GET', 'POST'])
@handle_database_errors
@log_function_call
def bill_history():
    """Billing History & Overdue bills page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        selected_customer = None
        chosen_customer_val = None

        if request.method == 'POST':
            chosen_customer_val = request.form.get('customer_choose', '')
        else:
            chosen_customer_val = request.args.get('customer_choose', '')

        customers = customer_service.get_all_customers()
        
        if chosen_customer_val and chosen_customer_val.strip() != '':
            try:
                target_id = int(chosen_customer_val.split()[0])
                # Find matching customer object across dataset
                selected_customer = next((c for c in customers if c.customer_id == target_id), None)
            except (ValueError, IndexError):
                pass

        all_billing_entries = billing_service.get_all_bills_with_status()
        
        unpaid_bills = 0
        for bill in all_billing_entries:
            if not bill['paid']:
                unpaid_bills += 1

        return render_template('administrator/bill_history.html',
                             jobs=all_billing_entries,
                             customers=customers,
                             selected_customer=selected_customer,
                             chosen_customer_val=chosen_customer_val,
                             unpaid_bills=unpaid_bills)

    except Exception as e:
        logger.error(f"Billing history page loading failed: {e}")
        flash('Failed to load billing details', 'error')
        # Empty array fallbacks to prevent Jinja engine structural layout breaks
        return render_template('administrator/bill_history.html',
                             jobs=[],
                             customers=[],
                             selected_customer=None,
                             chosen_customer_val='',
                             unpaid_bills=0)


@administrator_bp.route('/unpaid-bills', methods=['GET'])
@handle_database_errors
@log_function_call
def unpaid_bills():
    """Payment processing page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        customer_name = sanitize_input(request.args.get('customer', ''))

        filter_name = None
        if customer_name and customer_name != 'Choose...' and customer_name.strip() != '':
            filter_name = customer_name

        unpaid_bills = billing_service.get_unpaid_bills(filter_name)
        customers = customer_service.get_all_customers()
       
        total_overdue = 0
        for bill in unpaid_bills:
            if not bill.is_overdue:
                total_overdue += 1

        # Target file renamed to unpaid_bills.html
        return render_template('administrator/unpaid_bills.html',
                             unpaid_bills=unpaid_bills,
                             customers=customers,
                             total_overdue=total_overdue,
                             customer_name=customer_name)

    except Exception as e:
        logger.error(f"Payment processing page loading failed: {e}")
        flash('Failed to load payment processing page', 'error')
        return render_template('administrator/unpaid_bills.html',
                             customer_info=[],
                             customers=[],
                             total_overdue=0,
                             customer_name='')


@administrator_bp.route('/customers/<int:customer_id>/pay', methods=['POST'])
@handle_database_errors
def pay_customer_bills(customer_id):
    """Mark all bills for a customer as paid"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        success, errors, count = billing_service.mark_customer_bills_as_paid(customer_id)

        if success:
            flash(f'Successfully marked {count} bills as paid!', 'success')
        else:
            for error in errors:
                flash(error, 'error')

        return redirect(url_for('administrator.customer_list'))

    except Exception as e:
        logger.error(f"Failed to mark customer bills as paid: {e}")
        flash('Failed to mark payment, please try again later', 'error')
        return redirect(url_for('administrator.customer_list'))


@administrator_bp.route('/jobs/<int:job_id>/pay', methods=['POST'])
@handle_database_errors
def pay_single_bill(job_id):
    """Mark single work order as paid"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        success, errors = billing_service.mark_job_as_paid(job_id)

        if success:
            flash('Bill has been marked as paid!', 'success')
        else:
            for error in errors:
                flash(error, 'error')

        # Redirect based on source page
        return_page = sanitize_input(request.form.get('return_page', 'pay_bills'))
        if return_page == 'overdue_bills':
            return redirect(url_for('administrator.bill_history'))
        else:
            return redirect(url_for('administrator.unpaid_bills'))

    except Exception as e:
        logger.error(f"Failed to mark bill as paid: {e}")
        flash('Failed to mark payment, please try again later', 'error')
        return redirect(url_for('administrator.unpaid_bills'))


@administrator_bp.route('/reports')
@handle_database_errors
@log_function_call
def reports():
    """Reports page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    try:
        job_stats = job_service.get_job_statistics()
        billing_stats = billing_service.get_billing_statistics()

        total_customers = customer_service.get_all_customers()
        customers_with_unpaid = customer_service.get_customers_with_filter(has_unpaid=True)
        customers_with_overdue = customer_service.get_customers_with_filter(has_overdue=True)

        total_customers_count = len(total_customers)

        today = date.today()
        current_month_start = today.replace(day=1)
        last_month_end = current_month_start - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)

        total_customers_with_overdue = len(customers_with_overdue)
        total_customers_with_unpaid = len(customers_with_unpaid)
        total_customers_with_paid = total_customers_count - total_customers_with_unpaid
        
        actual_total_customers_with_unpaid = total_customers_with_unpaid - total_customers_with_overdue
        
        print(f"Reports Debug: total_customers_count {total_customers_count} total_customers_with_unpaid {total_customers_with_unpaid} ")
        
        report_data = {
            'job_stats': job_stats,
            'billing_stats': billing_stats,
            'customer_stats': {
                'total_customers': total_customers_count,
                'customers_with_unpaid': actual_total_customers_with_unpaid,
                'customers_with_overdue': total_customers_with_overdue,
                # FIX: Use the integer count here instead of the raw list
                'customer_payment_rate': f"{(total_customers_with_paid / total_customers_count * 100):.1f}%" if total_customers_count > 0 else "0%"
            },
            'period_info': {
                'current_month': current_month_start.strftime('%B %Y'),
                'last_month': last_month_start.strftime('%B %Y'),
                'generated_date': today.strftime('%Y-%m-%d')
            }
        }

        # FIX: Use ** unpacking so keys are passed directly to match the streamlined template
        return render_template('administrator/reports.html', **report_data)

    except Exception as e:
        logger.error(f"Reports page loading failed: {e}")
        flash('Failed to load reports', 'error')
        return render_template('administrator/reports.html',
                             report_data={})


# API endpoints
@administrator_bp.route('/api/customers/<int:customer_id>/billing-summary')
@handle_database_errors
def api_customer_billing_summary(customer_id):
    """API: Get customer billing summary"""
    try:
        summary = billing_service.get_customer_billing_summary(customer_id)
        return jsonify(summary)

    except Exception as e:
        logger.error(f"Failed to get customer billing summary: {e}")
        return jsonify({'error': 'Failed to get billing summary'}), 500


@administrator_bp.route('/api/billing/statistics')
@handle_database_errors
def api_billing_statistics():
    """API: Get billing statistics"""
    try:
        stats = billing_service.get_billing_statistics()
        return jsonify(stats)

    except Exception as e:
        logger.error(f"Failed to get billing statistics: {e}")
        return jsonify({'error': 'Failed to get statistics'}), 500


@administrator_bp.route('/api/dashboard/summary')
@handle_database_errors
def api_dashboard_summary():
    """API: Get dashboard summary"""
    try:
        job_stats = job_service.get_job_statistics()
        billing_stats = billing_service.get_billing_statistics()

        # Customer statistics
        total_customers = len(customer_service.get_all_customers())
        customers_with_unpaid = customer_service.get_customers_with_filter(has_unpaid=True)
        customers_with_overdue = customer_service.get_customers_with_filter(has_overdue=True)

        summary = {
            'jobs': job_stats,
            'billing': billing_stats,
            'customers': {
                'total': total_customers,
                'with_unpaid': len(customers_with_unpaid),
                'with_overdue': len(customers_with_overdue)
            },
            'alerts': {
                'overdue_bills': len(billing_service.get_overdue_bills()),
                'pending_jobs': job_stats.get('pending_jobs', 0)
            }
        }

        return jsonify(summary)

    except Exception as e:
        logger.error(f"Failed to get dashboard summary: {e}")
        return jsonify({'error': 'Failed to get summary'}), 500


@administrator_bp.route('/api/export/customers')
@handle_database_errors
def api_export_customers():
    """API: Export customer data"""
    try:
        customers = customer_service.get_all_customers()
        customer_data = []

        for c in customers:
            customer_info = c.to_dict()
            customer_info['total_unpaid'] = c.get_total_unpaid_amount()
            customer_info['has_overdue'] = c.has_overdue_bills()
            customer_data.append(customer_info)

        return jsonify({
            'data': customer_data,
            'export_date': date.today().isoformat(),
            'total_count': len(customer_data)
        })

    except Exception as e:
        logger.error(f"Failed to export customer data: {e}")
        return jsonify({'error': 'Failed to export data'}), 500


@administrator_bp.route('/api/customers/<int:customer_id>/summary')
@handle_database_errors
def api_customer_summary(customer_id):
    """API: Get customer summary"""
    try:
        customer = customer_service.get_customer_by_id(customer_id)
        if not customer:
            return jsonify({'error': 'Customer not found'}), 404

        stats = customer_service.get_customer_statistics(customer_id)
        return jsonify(stats)

    except Exception as e:
        logger.error(f"Failed to get customer summary: {e}")
        return jsonify({'error': 'Failed to get customer information'}), 500


# =============================================================================
# ORGANIZATION SETTINGS
# =============================================================================

@administrator_bp.route('/settings', methods=['GET', 'POST'])
@handle_database_errors
@log_function_call
def org_settings():
    """Organization settings page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.tenant import Tenant
    from app.extensions import db

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    tenant = Tenant.find_by_id(tenant_id)
    if not tenant:
        flash('Organization not found', 'error')
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        try:
            tenant.name = sanitize_input(request.form.get('name', tenant.name))
            tenant.email = sanitize_input(request.form.get('email', '')) or tenant.email
            tenant.phone = sanitize_input(request.form.get('phone', '')) or tenant.phone
            tenant.address = sanitize_input(request.form.get('address', '')) or tenant.address

            settings = tenant.settings or {}
            tax_rate = request.form.get('tax_rate')
            if tax_rate:
                try:
                    settings['tax_rate'] = float(tax_rate)
                except ValueError:
                    pass
            settings['currency'] = sanitize_input(request.form.get('currency', 'USD'))
            tenant.settings = settings

            session['current_tenant_name'] = tenant.name
            db.session.commit()
            flash('Organization settings updated!', 'success')
        except Exception as e:
            logger.error(f"Failed to update org settings: {e}")
            db.session.rollback()
            flash('Failed to update settings', 'error')

    return render_template('administrator/org_settings.html', tenant=tenant)


# =============================================================================
# TEAM MANAGEMENT
# =============================================================================

@administrator_bp.route('/team')
@handle_database_errors
@log_function_call
def team_members():
    """Team member management page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.tenant_membership import TenantMembership
    from app.models.user import User
    from app.extensions import db

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    try:
        memberships = db.session.execute(
            db.select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id
            ).order_by(TenantMembership.role, TenantMembership.created_at)
        ).scalars().all()

        members = []
        for m in memberships:
            user = User.find_by_id(m.user_id)
            if user:
                members.append({
                    'membership_id': m.id,
                    'user_id': m.user_id,
                    'username': user.username,
                    'email': user.email,
                    'role': m.role,
                    'status': m.status,
                    'is_default': m.is_default,
                    'accepted_at': m.accepted_at,
                    'invited_at': m.invited_at,
                })

        return render_template('administrator/team_members.html',
                             members=members,
                             available_roles=TenantMembership.VALID_ROLES)

    except Exception as e:
        logger.error(f"Failed to load team members: {e}")
        flash('Failed to load team members', 'error')
        return render_template('administrator/team_members.html',
                             members=[],
                             available_roles=[])


@administrator_bp.route('/team/invite', methods=['POST'])
@handle_database_errors
def invite_team_member():
    """Invite a new team member"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.services.tenant_service import TenantService

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    email = sanitize_input(request.form.get('email', ''))
    role = sanitize_input(request.form.get('role', 'viewer'))
    user_id = session.get('user_id')

    if not email:
        flash('Email is required', 'error')
        return redirect(url_for('administrator.team_members'))

    tenant_service = TenantService()
    success, errors, membership = tenant_service.invite_member(
        tenant_id=tenant_id,
        email=email,
        role=role,
        invited_by_user_id=user_id,
    )

    if success:
        flash(f'Invitation sent to {email}!', 'success')
    else:
        for error in errors:
            flash(error, 'error')

    return redirect(url_for('administrator.team_members'))


# =============================================================================
# SERVICE CATALOG
# =============================================================================

@administrator_bp.route('/services', methods=['GET', 'POST'])
@handle_database_errors
@log_function_call
def service_catalog():
    """Service catalog management"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.service import Service
    from app.extensions import db

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)

    if request.method == 'POST':
        action = request.form.get('action', 'add')

        if action == 'add':
            data = {
                'service_name': sanitize_input(request.form.get('service_name', '')),
                'cost': request.form.get('cost'),
            }
            validation = validate_service_data(data)
            if not validation.is_valid:
                for error in validation.get_errors():
                    flash(error, 'error')
            else:
                try:
                    service = Service(
                        tenant_id=tenant_id,
                        service_name=data['service_name'],
                        cost=float(data['cost']),
                        category=sanitize_input(request.form.get('category', 'General')),
                        description=sanitize_input(request.form.get('description', '')),
                        estimated_duration_minutes=request.form.get('estimated_duration', type=int),
                        is_active=True,
                    )
                    db.session.add(service)
                    db.session.commit()
                    flash(f'Service {data["service_name"]} added!', 'success')
                except Exception as e:
                    logger.error(f"Failed to add service: {e}")
                    db.session.rollback()
                    flash('Failed to add service', 'error')

        elif action == 'toggle':
            service_id = request.form.get('service_id', type=int)
            if service_id:
                service = Service.find_by_id(service_id)
                if service:
                    service.is_active = not service.is_active
                    db.session.commit()
                    status = 'activated' if service.is_active else 'deactivated'
                    flash(f'Service {status}!', 'success')

        return redirect(url_for('administrator.service_catalog'))

    # GET - load services
    try:
        g.current_tenant_id = tenant_id
        services = Service.get_all_sorted()
        return render_template('administrator/service_catalog.html', services=services)
    except Exception as e:
        logger.error(f"Failed to load service catalog: {e}")
        flash('Failed to load service catalog', 'error')
        return render_template('administrator/service_catalog.html', services=[])


# =============================================================================
# PARTS CATALOG
# =============================================================================

@administrator_bp.route('/parts', methods=['GET', 'POST'])
@handle_database_errors
@log_function_call
def parts_catalog():
    """Parts catalog management"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.part import Part
    from app.extensions import db

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)

    if request.method == 'POST':
        action = request.form.get('action', 'add')

        if action == 'add':
            data = {
                'part_name': sanitize_input(request.form.get('part_name', '')),
                'cost': request.form.get('cost'),
            }
            validation = validate_part_data(data)
            if not validation.is_valid:
                for error in validation.get_errors():
                    flash(error, 'error')
            else:
                try:
                    part = Part(
                        tenant_id=tenant_id,
                        part_name=data['part_name'],
                        cost=float(data['cost']),
                        sku=sanitize_input(request.form.get('sku', '')) or None,
                        category=sanitize_input(request.form.get('category', 'General')),
                        description=sanitize_input(request.form.get('description', '')),
                        supplier=sanitize_input(request.form.get('supplier', '')) or None,
                        is_active=True,
                    )
                    db.session.add(part)
                    db.session.commit()
                    flash(f'Part {data["part_name"]} added!', 'success')
                except Exception as e:
                    logger.error(f"Failed to add part: {e}")
                    db.session.rollback()
                    flash('Failed to add part', 'error')

        elif action == 'toggle':
            part_id = request.form.get('part_id', type=int)
            if part_id:
                part = Part.find_by_id(part_id)
                if part:
                    part.is_active = not part.is_active
                    db.session.commit()
                    status = 'activated' if part.is_active else 'deactivated'
                    flash(f'Part {status}!', 'success')

        return redirect(url_for('administrator.parts_catalog'))

    # GET - load parts
    try:
        g.current_tenant_id = tenant_id
        parts = Part.get_all_sorted()
        return render_template('administrator/parts_catalog.html', parts=parts)
    except Exception as e:
        logger.error(f"Failed to load parts catalog: {e}")
        flash('Failed to load parts catalog', 'error')
        return render_template('administrator/parts_catalog.html', parts=[])


# =============================================================================
# INVENTORY MANAGEMENT
# =============================================================================

@administrator_bp.route('/inventory')
@handle_database_errors
@log_function_call
def inventory():
    """Inventory dashboard"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.inventory import Inventory, InventoryTransaction
    from app.extensions import db

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    try:
        inventory_items = db.session.execute(
            db.select(Inventory).where(Inventory.tenant_id == tenant_id)
        ).scalars().all()

        # Get recent transactions
        recent_transactions = db.session.execute(
            db.select(InventoryTransaction)
            .where(InventoryTransaction.tenant_id == tenant_id)
            .order_by(InventoryTransaction.created_at.desc())
            .limit(20)
        ).scalars().all()

        # Identify low stock items
        low_stock = [item for item in inventory_items
                     if item.quantity_on_hand <= item.reorder_level]

        return render_template('administrator/inventory.html',
                             inventory_items=inventory_items,
                             recent_transactions=recent_transactions,
                             low_stock=low_stock,
                             total_items=len(inventory_items))

    except Exception as e:
        logger.error(f"Failed to load inventory: {e}")
        flash('Failed to load inventory', 'error')
        return render_template('administrator/inventory.html',
                             inventory_items=[],
                             recent_transactions=[],
                             low_stock=[],
                             total_items=0)


@administrator_bp.route('/inventory/adjust', methods=['POST'])
@handle_database_errors
def inventory_adjust():
    """Adjust inventory stock level"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.inventory import Inventory, InventoryTransaction
    from app.extensions import db

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    inventory_id = request.form.get('inventory_id', type=int)
    adjustment = request.form.get('quantity', type=int)
    transaction_type = sanitize_input(request.form.get('transaction_type', 'adjustment'))
    notes = sanitize_input(request.form.get('notes', ''))

    if not inventory_id or adjustment is None:
        flash('Invalid adjustment data', 'error')
        return redirect(url_for('administrator.inventory'))

    try:
        item = db.session.get(Inventory, inventory_id)
        if not item or item.tenant_id != tenant_id:
            flash('Inventory item not found', 'error')
            return redirect(url_for('administrator.inventory'))

        # Update quantity
        item.quantity_on_hand += adjustment

        # Record transaction
        transaction = InventoryTransaction(
            tenant_id=tenant_id,
            inventory_id=inventory_id,
            transaction_type=transaction_type,
            quantity=adjustment,
            performed_by=session.get('user_id'),
            notes=notes,
        )
        db.session.add(transaction)
        db.session.commit()

        flash(f'Inventory adjusted by {adjustment:+d} units', 'success')

    except Exception as e:
        logger.error(f"Failed to adjust inventory: {e}")
        db.session.rollback()
        flash('Failed to adjust inventory', 'error')

    return redirect(url_for('administrator.inventory'))


# =============================================================================
# SUBSCRIPTION MANAGEMENT
# =============================================================================

@administrator_bp.route('/subscription')
@handle_database_errors
@log_function_call
def subscription_management():
    """Subscription and plan management page"""
    redirect_response = require_admin_login()
    if redirect_response:
        return redirect_response

    from app.models.subscription import Subscription
    from app.models.tenant import Tenant
    from app.extensions import db

    tenant_id = session.get('current_tenant_id') or getattr(g, 'current_tenant_id', None)
    if not tenant_id:
        flash('No organization selected', 'error')
        return redirect(url_for('main.dashboard'))

    try:
        tenant = Tenant.find_by_id(tenant_id)
        subscription = db.session.execute(
            db.select(Subscription).where(Subscription.tenant_id == tenant_id)
        ).scalar_one_or_none()

        return render_template('administrator/subscription.html',
                             tenant=tenant,
                             subscription=subscription)

    except Exception as e:
        logger.error(f"Failed to load subscription info: {e}")
        flash('Failed to load subscription information', 'error')
        return render_template('administrator/subscription.html',
                             tenant=None,
                             subscription=None)
