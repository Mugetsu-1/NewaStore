from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Product, Category


class ProductSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.8
    limit = 5000  # urls per sitemap page

    def items(self):
        # newest first; capped so a 60k+ catalog doesn't build giant sitemaps
        return (Product.objects.filter(is_active=True)
                .order_by('-published_at')[:10000])

    def lastmod(self, obj):
        return obj.updated_at


class CategorySitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.7

    def items(self):
        return Category.objects.filter(is_active=True)


class StaticViewSitemap(Sitemap):
    priority = 0.5
    changefreq = 'monthly'

    def items(self):
        return ['home', 'product_list', 'about', 'contact', 'faq', 'privacy', 'terms', 'shipping_returns']

    def location(self, item):
        return reverse(item)