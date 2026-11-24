from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import Cart, CartItem, Product, ProductVariant


MAX_CART_QUANTITY = 99


class CartManager:
    """Handles cart operations for both authenticated users and guests."""

    def __init__(self, request):
        self.request = request
        self.session = request.session
        self._cart = None

    @property
    def cart(self):
        if self._cart is not None:
            return self._cart
        if self.request.user.is_authenticated:
            cart, _ = Cart.objects.get_or_create(user=self.request.user)
            session_key = self.session.session_key
            if session_key:
                guest_cart = Cart.objects.filter(session_key=session_key, user__isnull=True).first()
                if guest_cart and guest_cart.pk != cart.pk:
                    for item in guest_cart.items.all():
                        existing = cart.items.filter(product=item.product, variant=item.variant).first()
                        if existing:
                            existing.quantity = min(
                                MAX_CART_QUANTITY, existing.quantity + item.quantity
                            )
                            existing.save(update_fields=['quantity', 'updated_at'])
                        else:
                            item.cart = cart
                            item.save()
                    guest_cart.delete()
        else:
            if not self.session.session_key:
                self.session.create()
            session_key = self.session.session_key
            cart, _ = Cart.objects.get_or_create(session_key=session_key, user__isnull=True)
        self._cart = cart
        return cart

    def add(self, product, quantity=1, variant=None, update_quantity=False):
        quantity = self._validated_quantity(product, quantity, variant)
        with transaction.atomic():
            item, created = CartItem.objects.select_for_update().get_or_create(
                cart=self.cart, product=product, variant=variant,
                defaults={'quantity': quantity},
            )
            if created:
                return item
            new_quantity = quantity if update_quantity else item.quantity + quantity
            item.quantity = self._validated_quantity(product, new_quantity, variant)
            item.save(update_fields=['quantity', 'updated_at'])
            return item

    def remove(self, item_id):
        CartItem.objects.filter(id=item_id, cart=self.cart).delete()

    def update(self, item_id, quantity):
        with transaction.atomic():
            item = CartItem.objects.select_for_update(of=('self',)).select_related(
                'product', 'variant'
            ).filter(id=item_id, cart=self.cart).first()
            if item:
                if quantity <= 0:
                    item.delete()
                else:
                    item.quantity = self._validated_quantity(
                        item.product, quantity, item.variant
                    )
                    item.save(update_fields=['quantity', 'updated_at'])
                return item
            return None

    @staticmethod
    def _validated_quantity(product, quantity, variant=None):
        try:
            quantity = int(quantity)
        except (TypeError, ValueError) as exc:
            raise ValidationError('Enter a valid quantity.') from exc
        if quantity < 1 or quantity > MAX_CART_QUANTITY:
            raise ValidationError(
                f'Quantity must be between 1 and {MAX_CART_QUANTITY}.'
            )
        if variant is not None and (
            variant.product_id != product.id or not variant.is_active
        ):
            raise ValidationError('The selected product option is not available.')
        if variant is None and product.variants.filter(is_active=True).exists():
            raise ValidationError('Choose a product option before adding this item.')
        if not product.is_active:
            raise ValidationError('This product is not available.')
        if product.track_inventory and not product.allow_backorder:
            available = variant.stock_quantity if variant is not None else product.stock_quantity
            if quantity > available:
                raise ValidationError(f'Only {available} item(s) are available.')
        return quantity

    def clear(self):
        self.cart.items.all().delete()

    def apply_coupon(self, coupon):
        self.cart.coupon = coupon
        self.cart.save()

    def remove_coupon(self):
        self.cart.coupon = None
        self.cart.save()

    @property
    def items(self):
        return self.cart.items.select_related('product', 'variant').all()

    @property
    def count(self):
        return self.cart.items_count

    @property
    def subtotal(self):
        return self.cart.subtotal

    @property
    def discount(self):
        return self.cart.discount_amount

    @property
    def total(self):
        return self.cart.total

    @property
    def is_empty(self):
        return not self.items.exists()
