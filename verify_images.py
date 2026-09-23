import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "newastore.settings")
django.setup()

from django.conf import settings
from pathlib import Path

media_root = Path(settings.MEDIA_ROOT)
print(f"MEDIA_ROOT: {media_root}")
print(f"Exits: {media_root.exists()}")
if media_root.exists():
    images_dir = media_root / "product_images"
    print(f"product_images dir: {images_dir.exists()}")
    if images_dir.exists():
        files = list(images_dir.iterdir())
        print(f"Files in product_images: {len(files)}")
        for f in files[:5]:
            print(f"  {f.name} ({f.stat().st_size} bytes)")
        print(f"  ... and {len(files)-5} more" if len(files) > 5 else "")
    else:
        print("product_images/ directory not created yet")
else:
    print("MEDIA_ROOT does not exist")

from store.models import Product, ProductImage
sample = Product.objects.filter(images__isnull=False).first()
if sample:
    img = sample.images.first()
    print(f"\nSample product: {sample.name}")
    print(f"  Image src_url: {img.src_url}")
    print(f"  Image file: {img.image.name if img.image else 'None'}")
    print(f"  Image on disk: {img.image.path if img.image else 'N/A'}")
else:
    print("\nNo products with images found yet")
