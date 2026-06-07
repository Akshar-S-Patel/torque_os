from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.orm import joinedload
from app.extensions import db
from app.models.inventory import Inventory, InventoryTransaction
from app.models.part import Part
from app.models.job import JobPart

class InventoryService:
    """
    Handles business logic and database queries for Inventory management.
    """
    
    PART_CATEGORY_CONFIG = {
        'General': 'bg-bitbucket text-white',
        'Engine': 'bg-danger text-white',
        'Brakes': 'bg-warning text-dark',
        'Suspension': 'bg-info text-white',
        'Electrical': 'bg-success text-white',
        'Body': 'bg-purple text-white',
        'Fluids': 'bg-primary text-white'
    }
    
    @staticmethod
    def get_category_config():
        return InventoryService.PART_CATEGORY_CONFIG

    @staticmethod
    def get_tracked_items(tenant_id: int):
        """Returns all parts currently tracked in inventory."""
        return db.session.execute(
            db.select(Inventory)
            .where(Inventory.tenant_id == tenant_id)
            .options(joinedload(Inventory.part))
        ).scalars().all()

    @staticmethod
    def get_untracked_parts_paginated(tenant_id: int, page: int = 1, per_page: int = 10):
        """Returns paginated parts that exist in catalog but have no inventory record."""
        # 1. Find IDs of tracked parts
        tracked_subquery = db.select(Inventory.part_id).where(Inventory.tenant_id == tenant_id)
        
        # 2. Filter parts not in that subquery
        query = db.select(Part).where(
            Part.tenant_id == tenant_id,
            ~Part.part_id.in_(tracked_subquery)
        )
        return db.paginate(query, page=page, per_page=per_page, error_out=False)

    @staticmethod
    def get_active_procurement_requests() -> dict:
        """
        Returns all job parts flagged for ordering, grouped by part_id.
        Returns: { part_id: [JobPart, JobPart] }
        """
        active_orders = db.session.execute(
            db.select(JobPart).where(JobPart.status.in_(['needs_order', 'ordered']))
        ).scalars().all()
        
        orders_by_part = {}
        for order in active_orders:
            orders_by_part.setdefault(order.part_id, []).append(order)
            
        return orders_by_part

    @staticmethod
    def get_recent_transactions(tenant_id: int, limit: int = 20):
        """Returns the most recent inventory movements."""
        return db.session.execute(
            db.select(InventoryTransaction)
            .where(InventoryTransaction.tenant_id == tenant_id)
            .order_by(InventoryTransaction.created_at.desc())
            .limit(limit)
        ).scalars().all()

    @staticmethod
    def fulfill_order_request(job_id: int, part_id: int, notes: str) -> bool:
        """Marks a needed part as ordered and attaches tracking notes."""
        job_part = db.session.get(JobPart, (job_id, part_id))
        if not job_part:
            return False
            
        job_part.status = 'ordered'
        job_part.order_notes = notes
        job_part.ordered_at = datetime.now(timezone.utc)
        db.session.commit()
        return True

    @staticmethod
    def setup_or_update_tracking(tenant_id: int, part_id: int, location: str, stock: int, reorder: int) -> None:
        inventory = db.session.execute(
            db.select(Inventory).where(
                Inventory.part_id == part_id, 
                Inventory.tenant_id == tenant_id
            )
        ).scalar_one_or_none()

        # If stock is 0 or less, we want it in the Untracked Catalog.
        if stock <= 0:
            if inventory:
                db.session.delete(inventory)
                db.session.commit()
            return

        if not inventory:
            inventory = Inventory(
                tenant_id=tenant_id,
                part_id=part_id,
                quantity_on_hand=stock,
                reorder_level=reorder,
                location=location
            )
            db.session.add(inventory)
            db.session.flush() 
            
            db.session.add(InventoryTransaction(
                tenant_id=tenant_id, 
                inventory_id=inventory.inventory_id,
                transaction_type='received', 
                quantity=stock, 
                notes='Initial Stock Setup'
            ))
        else:
            inventory.location = location
            inventory.quantity_on_hand = stock
            inventory.reorder_level = reorder

        db.session.commit()
        
    @staticmethod
    def create_part_with_tracking(tenant_id: int, data: dict) -> None:
        """Creates a new part and sets up initial inventory if stock > 0."""
        
        # Clean up empty strings from the frontend to insert pure NULLs
        def clean_val(val):
            return val if val and str(val).strip() else None

        try:
            # 1. Create the Part
            new_part = Part(
                tenant_id=tenant_id,
                part_name=data['part_name'],
                cost=Decimal(str(data['cost'])), # Strict casting to Decimal
                sku=clean_val(data.get('sku')),
                category=clean_val(data.get('category')),
                supplier=clean_val(data.get('supplier')),
                is_active=True
            )
            db.session.add(new_part)
            db.session.flush() # Generates the new part_id

            # 2. Create Inventory & Transaction (only if stock is provided)
            stock = data.get('stock', 0)
            if stock > 0:
                new_inventory = Inventory(
                    tenant_id=tenant_id,
                    part_id=new_part.part_id,
                    quantity_on_hand=stock,
                    reorder_level=data.get('reorder', 0),
                    location=clean_val(data.get('location'))
                )
                db.session.add(new_inventory)
                db.session.flush() # Generates the new inventory_id

                transaction = InventoryTransaction(
                    tenant_id=tenant_id,
                    inventory_id=new_inventory.inventory_id,
                    transaction_type='received',
                    quantity=stock,
                    notes='Initial Stock Setup via Quick Add'
                )
                db.session.add(transaction)

            db.session.commit()
            
        except Exception as e:
            db.session.rollback()
            raise e    