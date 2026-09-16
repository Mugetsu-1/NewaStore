import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'newastore.settings')
import django
django.setup()

from django.test import Client
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from store.models import Product, Cart, Order, ProductImage
import re

print('=== ADMIN USER ===')
admin_user = User.objects.filter(is_superuser=True).first()
if admin_user:
    print(f'Admin: {admin_user.username}')
    print(f'is_staff: {admin_user.is_staff}')
    print(f'is_superuser: {admin_user.is_superuser}')
    logged_in = authenticate(username='admin', password='admin123')
    print(f'Login with admin123: {logged_in is not None}')

print()
print('=== PRODUCT IMAGES ===')
total = Product.objects.count()
with_images = Product.objects.filter(images__isnull=False).distinct().count()
no_images = total - with_images
print(f'Total products: {total}')
print(f'Products with images: {with_images}')
print(f'Products without images: {no_images}')

# Sample image URLs
print('\n=== SAMPLE IMAGE URLs ===')
products_with_images = Product.objects.filter(images__isnull=False).distinct()[:5]
for p in products_with_images:
    img = p.images.first()
    if img:
        src = img.src_url or 'No URL'
        print(f'{p.name[:40]}: {src[:100]}')

# Check recent test results
print('\n=== RECENT TESTS ===')
print('Run: python manage.py test store --verbosity=1')
print('(See test output below)')
