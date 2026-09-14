from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.contrib.auth.models import User

from .models import Order, OrderStatusHistory, Wishlist, Product


@receiver(post_save, sender=User)
def create_user_wishlist(sender, instance, created, **kwargs):
    if created:
        Wishlist.objects.get_or_create(user=instance)


@receiver(pre_save, sender=Order)
def track_order_status_change(sender, instance, **kwargs):
    if not instance.pk:
        return
    try:
        old = Order.objects.get(pk=instance.pk)
    except Order.DoesNotExist:
        return
    if old.status != instance.status:
        OrderStatusHistory.objects.create(
            order=instance,
            status=instance.status,
            notes=f'Status changed from {old.status} to {instance.status}',
        )
