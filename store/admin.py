from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import (
    Category, Tag, Product, ProductImage, ProductVariant, Review, ReviewImage,
    Coupon, CouponUsage, Address, Cart, CartItem, Wishlist, WishlistItem,
    Order, OrderItem, OrderStatusHistory, ShippingMethod, NewsletterSubscriber,
    ContactMessage, SiteSettings
)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ['image', 'external_url', 'alt_text', 'is_primary', 'sort_order']
    readonly_fields = ['image_preview']

    def image_preview(self, obj):
        if obj and obj.src_url:
            return format_html('<img src="{}" style="max-height: 50px;" />', obj.src_url)
        return '-'
    image_preview.short_description = 'Preview'


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ['name', 'sku', 'price_adjustment', 'stock_quantity', 'is_active', 'attributes', 'sort_order']
    readonly_fields = ['sku']


class ReviewImageInline(admin.TabularInline):
    model = ReviewImage
    extra = 0
    readonly_fields = ['image_preview', 'created_at']
    fields = ['image', 'image_preview', 'created_at']

    def image_preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="max-height: 50px;" />', obj.image.url)
        return '-'
    image_preview.short_description = 'Preview'


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'parent', 'is_active', 'product_count', 'sort_order', 'created_at']
    list_filter = ['is_active', 'parent']
    search_fields = ['name', 'slug', 'description']
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ['is_active', 'sort_order']
    list_select_related = ['parent']

    def product_count(self, obj):
        return obj.products.count()
    product_count.short_description = 'Products'


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'product_count']
    search_fields = ['name']
    prepopulated_fields = {'slug': ('name',)}

    def product_count(self, obj):
        return obj.products.count()
    product_count.short_description = 'Products'


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['image_thumbnail', 'name', 'sku', 'category', 'price', 'discount_price',
                    'data_source', 'stock_quantity',
                    'is_active', 'is_featured', 'is_in_stock', 'created_at']
    list_filter = ['is_active', 'is_featured', 'is_digital', 'category', 'data_source', 'created_at']
    search_fields = ['name', 'sku', 'barcode', 'steam_app_id']
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ['is_active', 'is_featured']
    list_select_related = ['category']
    filter_horizontal = ['tags']
    inlines = [ProductImageInline, ProductVariantInline]
    readonly_fields = ['sku', 'created_at', 'updated_at', 'average_rating', 'review_count']
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'slug', 'sku', 'barcode', 'category', 'tags')
        }),
        ('Descriptions', {
            'fields': ('short_description', 'description')
        }),
        ('Pricing', {
            'fields': ('price', 'discount_price', 'cost_price')
        }),
        ('External Data', {
            'fields': ('steam_app_id', 'external_url', 'metacritic_score', 'data_source', 'steam_enriched'),
            'classes': ('collapse',)
        }),
        ('Inventory', {
            'fields': ('stock_quantity', 'low_stock_threshold', 'track_inventory', 'allow_backorder', 'weight', 'dimensions')
        }),
        ('Settings', {
            'fields': ('is_active', 'is_featured', 'is_digital', 'requires_shipping')
        }),
        ('SEO', {
            'fields': ('meta_title', 'meta_description', 'meta_keywords'),
            'classes': ('collapse',)
        }),
        ('Statistics', {
            'fields': ('average_rating', 'review_count', 'created_at', 'updated_at', 'published_at'),
            'classes': ('collapse',)
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related('images')

    def image_thumbnail(self, obj):
        images = list(obj.images.all())
        primary_image = next((i for i in images if i.is_primary), images[0] if images else None)
        if primary_image and primary_image.src_url:
            return format_html('<img src="{}" style="max-height: 40px;" />', primary_image.src_url)
        return '-'
    image_thumbnail.short_description = 'Image'

    def is_in_stock(self, obj):
        from django.utils.safestring import mark_safe
        if obj.is_in_stock:
            return mark_safe('<span style="color: green;">✓ In Stock</span>')
        return mark_safe('<span style="color: red;">✗ Out of Stock</span>')
    is_in_stock.short_description = 'Stock Status'


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ['product', 'image_preview', 'alt_text', 'is_primary', 'sort_order', 'created_at']
    list_filter = ['is_primary', 'product__category']
    search_fields = ['product__name', 'alt_text']
    list_editable = ['is_primary', 'sort_order']
    list_select_related = ['product']

    def image_preview(self, obj):
        if obj.src_url:
            return format_html('<img src="{}" style="max-height: 50px;" />', obj.src_url)
        return '-'
    image_preview.short_description = 'Preview'


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ['product', 'name', 'sku', 'final_price', 'stock_quantity', 'is_active', 'sort_order']
    list_filter = ['is_active', 'product__category']
    search_fields = ['name', 'sku', 'product__name']
    list_editable = ['is_active', 'sort_order']
    list_select_related = ['product']

    def final_price(self, obj):
        return obj.final_price
    final_price.short_description = 'Final Price'


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['product', 'user', 'rating', 'title', 'is_approved', 'is_verified_purchase', 'helpful_votes', 'created_at']
    list_filter = ['is_approved', 'is_verified_purchase', 'rating', 'product__category']
    search_fields = ['product__name', 'user__username', 'title', 'comment']
    list_editable = ['is_approved']
    list_select_related = ['product', 'user']
    inlines = [ReviewImageInline]
    readonly_fields = ['created_at', 'updated_at']

    actions = ['approve_reviews', 'disapprove_reviews']

    def approve_reviews(self, request, queryset):
        queryset.update(is_approved=True)
    approve_reviews.short_description = 'Approve selected reviews'

    def disapprove_reviews(self, request, queryset):
        queryset.update(is_approved=False)
    disapprove_reviews.short_description = 'Disapprove selected reviews'


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'discount_type', 'discount_value', 'minimum_amount', 'usage_limit',
                    'used_count', 'valid_from', 'valid_until', 'is_active']
    list_filter = ['discount_type', 'is_active', 'valid_from', 'valid_until']
    search_fields = ['code', 'name', 'description']
    list_editable = ['is_active']
    # Product M2Ms use autocomplete lookups — a filter_horizontal select would
    # render one option per product (tens of thousands) and hang the page.
    filter_horizontal = ['applicable_categories']
    autocomplete_fields = ['applicable_products', 'excluded_products']
    readonly_fields = ['used_count', 'created_at', 'updated_at']


@admin.register(CouponUsage)
class CouponUsageAdmin(admin.ModelAdmin):
    list_display = ['coupon', 'user', 'order', 'discount_amount', 'used_at']
    list_filter = ['coupon', 'used_at']
    search_fields = ['coupon__code', 'user__username']
    readonly_fields = ['coupon', 'user', 'order', 'discount_amount', 'used_at']


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ['user', 'address_type', 'full_name', 'city', 'state', 'country', 'is_default', 'created_at']
    list_filter = ['address_type', 'is_default', 'country', 'state']
    search_fields = ['user__username', 'full_name', 'city', 'phone', 'email']
    list_editable = ['is_default']
    list_select_related = ['user']


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    readonly_fields = ['product', 'variant', 'quantity', 'unit_price', 'total_price', 'created_at']
    fields = ['product', 'variant', 'quantity', 'unit_price', 'total_price']


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ['user', 'session_key', 'coupon', 'items_count', 'subtotal', 'discount_amount', 'total', 'updated_at']
    list_filter = ['coupon', 'created_at']
    search_fields = ['user__username', 'session_key']
    readonly_fields = ['created_at', 'updated_at', 'items_count', 'subtotal', 'discount_amount', 'total']
    inlines = [CartItemInline]


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ['user', 'items_count', 'created_at']
    search_fields = ['user__username']

    def items_count(self, obj):
        return obj.items.count()
    items_count.short_description = 'Items'


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ['product', 'variant', 'product_name', 'variant_name', 'unit_price', 'quantity', 'total_price']
    fields = ['product', 'variant', 'product_name', 'variant_name', 'unit_price', 'quantity', 'total_price']


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    readonly_fields = ['status', 'notes', 'created_by', 'created_at']
    fields = ['status', 'notes', 'created_by', 'created_at']
    ordering = ['-created_at']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['order_number', 'user', 'guest_email', 'status', 'payment_status', 'payment_method', 
                    'total', 'created_at']
    list_filter = ['status', 'payment_status', 'payment_method', 'created_at']
    search_fields = ['order_number', 'user__username', 'guest_email', 'guest_phone', 'billing_address__full_name']
    list_editable = ['status', 'payment_status']
    list_select_related = ['user', 'coupon']
    inlines = [OrderItemInline, OrderStatusHistoryInline]
    readonly_fields = ['order_number', 'created_at', 'updated_at', 'confirmed_at', 'shipped_at', 'delivered_at',
                       'ip_address', 'user_agent']
    fieldsets = (
        ('Order Information', {
            'fields': ('order_number', 'user', 'guest_email', 'guest_phone', 'status', 'payment_status', 'payment_method', 
                       'payment_transaction_id', 'coupon')
        }),
        ('Pricing', {
            'fields': ('subtotal', 'discount_amount', 'shipping_cost', 'tax_amount', 'total')
        }),
        ('Addresses', {
            'fields': ('billing_address', 'shipping_address'),
            'classes': ('collapse',)
        }),
        ('Shipping', {
            'fields': ('shipping_method', 'tracking_number', 'tracking_url'),
            'classes': ('collapse',)
        }),
        ('Notes', {
            'fields': ('notes', 'internal_notes'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('ip_address', 'user_agent', 'created_at', 'updated_at', 'confirmed_at', 'shipped_at', 'delivered_at'),
            'classes': ('collapse',)
        }),
    )

    actions = ['mark_confirmed', 'mark_processing', 'mark_shipped', 'mark_delivered', 'mark_cancelled']

    def mark_confirmed(self, request, queryset):
        for order in queryset:
            order.status = 'confirmed'
            order.save()
    mark_confirmed.short_description = 'Mark as Confirmed'

    def mark_processing(self, request, queryset):
        for order in queryset:
            order.status = 'processing'
            order.save()
    mark_processing.short_description = 'Mark as Processing'

    def mark_shipped(self, request, queryset):
        from django.utils import timezone
        for order in queryset:
            order.status = 'shipped'
            order.shipped_at = timezone.now()
            order.save()
    mark_shipped.short_description = 'Mark as Shipped'

    def mark_delivered(self, request, queryset):
        from django.utils import timezone
        for order in queryset:
            order.status = 'delivered'
            order.delivered_at = timezone.now()
            order.save()
    mark_delivered.short_description = 'Mark as Delivered'

    def mark_cancelled(self, request, queryset):
        for order in queryset:
            order.status = 'cancelled'
            order.save()
    mark_cancelled.short_description = 'Mark as Cancelled'


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ['order', 'product_name', 'variant_name', 'unit_price', 'quantity', 'total_price']
    search_fields = ['order__order_number', 'product_name', 'product_sku']
    list_select_related = ['order']


@admin.register(OrderStatusHistory)
class OrderStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ['order', 'status', 'created_by', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['order__order_number']
    readonly_fields = ['order', 'status', 'notes', 'created_by', 'created_at']


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    list_display = ['name', 'price', 'estimated_days_min', 'estimated_days_max', 'is_active', 'sort_order', 'free_shipping_threshold']
    list_filter = ['is_active']
    list_editable = ['is_active', 'sort_order']
    search_fields = ['name', 'description']


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ['email', 'is_active', 'subscribed_at', 'unsubscribed_at', 'source']
    list_filter = ['is_active', 'source', 'subscribed_at']
    search_fields = ['email']
    list_editable = ['is_active']
    readonly_fields = ['subscribed_at', 'unsubscribed_at']
    actions = ['activate_subscribers', 'deactivate_subscribers']

    def activate_subscribers(self, request, queryset):
        queryset.update(is_active=True, unsubscribed_at=None)
    activate_subscribers.short_description = 'Activate selected subscribers'

    def deactivate_subscribers(self, request, queryset):
        from django.utils import timezone
        queryset.update(is_active=False, unsubscribed_at=timezone.now())
    deactivate_subscribers.short_description = 'Deactivate selected subscribers'


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'subject', 'is_read', 'is_replied', 'created_at']
    list_filter = ['is_read', 'is_replied', 'created_at']
    search_fields = ['name', 'email', 'subject', 'message']
    list_editable = ['is_read', 'is_replied']
    readonly_fields = ['created_at']
    actions = ['mark_read', 'mark_unread']

    def mark_read(self, request, queryset):
        queryset.update(is_read=True)
    mark_read.short_description = 'Mark as Read'

    def mark_unread(self, request, queryset):
        queryset.update(is_read=False)
    mark_unread.short_description = 'Mark as Unread'


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ['site_name', 'email', 'phone', 'currency', 'tax_rate', 'maintenance_mode', 'updated_at']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('General', {
            'fields': ('site_name', 'site_tagline', 'logo', 'favicon')
        }),
        ('Contact Information', {
            'fields': ('email', 'phone', 'address')
        }),
        ('Social Media', {
            'fields': ('social_facebook', 'social_twitter', 'social_instagram', 'social_youtube', 'social_linkedin'),
            'classes': ('collapse',)
        }),
        ('SEO', {
            'fields': ('meta_title', 'meta_description', 'meta_keywords', 'google_analytics_id', 'facebook_pixel_id'),
            'classes': ('collapse',)
        }),
        ('Settings', {
            'fields': ('maintenance_mode', 'maintenance_message', 'free_shipping_threshold', 'tax_rate', 
                       'currency', 'currency_symbol', 'allow_guest_checkout', 'require_account_for_digital')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False