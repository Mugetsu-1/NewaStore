from django.db import migrations, models


def remap_digital_statuses(apps, schema_editor):
    """Collapse physical-fulfillment statuses onto the digital lifecycle.

    shipped/delivered -> completed, returned -> refunded, and the removed
    cod payment method -> esewa, so no row is left on a value that the new
    choices no longer allow.
    """
    Order = apps.get_model('store', 'Order')
    OrderStatusHistory = apps.get_model('store', 'OrderStatusHistory')
    Order.objects.filter(status__in=['shipped', 'delivered']).update(status='completed')
    Order.objects.filter(status='returned').update(status='refunded')
    Order.objects.filter(payment_method='cod').update(payment_method='esewa')
    OrderStatusHistory.objects.filter(status__in=['shipped', 'delivered']).update(status='completed')
    OrderStatusHistory.objects.filter(status='returned').update(status='refunded')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0006_productimage_art_unavailable'),
    ]

    operations = [
        migrations.RunPython(remap_digital_statuses, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='address',
            name='user',
        ),
        migrations.DeleteModel(
            name='ShippingMethod',
        ),
        migrations.RemoveField(
            model_name='order',
            name='delivered_at',
        ),
        migrations.RemoveField(
            model_name='order',
            name='shipped_at',
        ),
        migrations.RemoveField(
            model_name='order',
            name='shipping_address',
        ),
        migrations.RemoveField(
            model_name='order',
            name='shipping_cost',
        ),
        migrations.RemoveField(
            model_name='order',
            name='shipping_method',
        ),
        migrations.RemoveField(
            model_name='order',
            name='tracking_number',
        ),
        migrations.RemoveField(
            model_name='order',
            name='tracking_url',
        ),
        migrations.RemoveField(
            model_name='product',
            name='dimensions',
        ),
        migrations.RemoveField(
            model_name='product',
            name='requires_shipping',
        ),
        migrations.RemoveField(
            model_name='product',
            name='weight',
        ),
        migrations.RemoveField(
            model_name='sitesettings',
            name='free_shipping_threshold',
        ),
        migrations.AlterField(
            model_name='coupon',
            name='discount_type',
            field=models.CharField(choices=[('percentage', 'Percentage'), ('fixed', 'Fixed Amount')], max_length=20),
        ),
        migrations.AlterField(
            model_name='order',
            name='payment_method',
            field=models.CharField(choices=[('esewa', 'eSewa'), ('khalti', 'Khalti'), ('nay_bank', 'Nay Bank Transfer'), ('bank_transfer', 'Bank Transfer (legacy)'), ('stripe', 'Stripe'), ('paypal', 'PayPal')], default='esewa', max_length=20),
        ),
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(choices=[('pending', 'Pending'), ('confirmed', 'Confirmed'), ('processing', 'Processing'), ('completed', 'Completed'), ('cancelled', 'Cancelled'), ('refunded', 'Refunded')], default='pending', max_length=20),
        ),
        migrations.AlterField(
            model_name='orderstatushistory',
            name='status',
            field=models.CharField(choices=[('pending', 'Pending'), ('confirmed', 'Confirmed'), ('processing', 'Processing'), ('completed', 'Completed'), ('cancelled', 'Cancelled'), ('refunded', 'Refunded')], max_length=20),
        ),
        migrations.DeleteModel(
            name='Address',
        ),
    ]
