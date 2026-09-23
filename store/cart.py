from decimal import Decimal
from .models import Cart, CartItem, Product, ProductVariant


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
                            existing.quantity += item.quantity
                            existing.save()
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
        item, created = CartItem.objects.get_or_create(
            cart=self.cart, product=product, variant=variant,
            defaults={'quantity': quantity}
        )
        if not created and update_quantity:
            item.quantity = quantity
        elif not created:
            item.quantity += quantity
        item.save()
        return item

    def remove(self, item_id):
        CartItem.objects.filter(id=item_id, cart=self.cart).delete()

    def update(self, item_id, quantity):
        item = CartItem.objects.filter(id=item_id, cart=self.cart).first()
        if item:
            if quantity <= 0:
                item.delete()
            else:
                item.quantity = quantity
                item.save()
            return item
        return None

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
