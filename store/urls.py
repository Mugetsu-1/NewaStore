from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('shop/', views.product_list, name='product_list'),
    path('search/', views.search, name='search'),
    path('search/suggest/', views.search_suggest, name='search_suggest'),
    path('product/<slug:slug>/', views.product_detail, name='product_detail'),
    path('product/<slug:slug>/quick-view/', views.quick_view, name='quick_view'),
    path('category/<slug:slug>/', views.category_detail, name='category_detail'),
    path('tag/<slug:slug>/', views.tag_detail, name='tag_detail'),

    path('cart/', views.cart_view, name='cart'),
    path('cart/mini/', views.cart_mini, name='cart_mini'),
    path('cart/add/<int:product_id>/', views.add_to_cart, name='add_to_cart'),
    path('cart/update/<int:item_id>/', views.update_cart, name='update_cart'),
    path('cart/remove/<int:item_id>/', views.remove_from_cart, name='remove_from_cart'),
    path('cart/clear/', views.clear_cart, name='clear_cart'),
    path('cart/coupon/apply/', views.apply_coupon, name='apply_coupon'),
    path('cart/coupon/remove/', views.remove_coupon, name='remove_coupon'),

    path('checkout/', views.checkout, name='checkout'),
    path('payment/simulate/<str:order_number>/<str:gateway>/', views.simulate_payment, name='simulate_payment'),
    path('esewa-verify/', views.esewa_verify, name='esewa_verify'),
    path('khalti-verify/', views.khalti_verify, name='khalti_verify'),
    path('payment/stripe/<str:order_number>/', views.stripe_checkout, name='stripe_checkout'),
    path('payment/stripe/<str:order_number>/intent/', views.stripe_create_intent, name='stripe_create_intent'),
    path('payment/stripe/<str:order_number>/success/', views.stripe_success, name='stripe_success'),
    path('webhooks/stripe/', views.stripe_webhook, name='stripe_webhook'),
    path('payment/paypal/<str:order_number>/', views.paypal_checkout, name='paypal_checkout'),
    path('payment/paypal/<str:order_number>/create/', views.paypal_create_order, name='paypal_create_order'),
    path('payment/paypal/<str:order_number>/capture/', views.paypal_capture, name='paypal_capture'),
    path('webhooks/paypal/', views.paypal_webhook, name='paypal_webhook'),
    path('payment/failed/<str:order_number>/', views.payment_failed, name='payment_failed'),
    path('order/success/<str:order_number>/', views.order_success, name='order_success'),

    path('orders/', views.order_history, name='order_history'),
    path('orders/<str:order_number>/', views.order_detail, name='order_detail'),
    path('orders/<str:order_number>/invoice/', views.download_invoice, name='download_invoice'),
    path('orders/<str:order_number>/cancel/', views.cancel_order, name='cancel_order'),
    path('orders/<str:order_number>/reorder/', views.reorder, name='reorder'),

    path('profile/', views.profile, name='profile'),
    path('profile/edit/', views.profile_edit, name='profile_edit'),
    path('profile/password/', views.change_password, name='change_password'),
    path('reviews/', views.my_reviews, name='my_reviews'),

    path('wishlist/', views.wishlist_view, name='wishlist'),
    path('wishlist/toggle/<int:product_id>/', views.wishlist_toggle, name='wishlist_toggle'),
    path('wishlist/remove/<int:item_id>/', views.wishlist_remove, name='wishlist_remove'),
    path('wishlist/move-to-cart/<int:item_id>/', views.wishlist_move_to_cart, name='wishlist_move_to_cart'),

    path('product/<slug:slug>/review/', views.add_review, name='add_review'),
    path('review/<int:review_id>/helpful/', views.mark_review_helpful, name='mark_review_helpful'),

    path('register/', views.register, name='register'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('password-reset/', views.CustomPasswordResetView.as_view(), name='password_reset'),
    path('password-reset/done/', views.CustomPasswordResetDoneView.as_view(), name='password_reset_done'),
    path('password-reset/confirm/<uidb64>/<token>/', views.CustomPasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('password-reset/complete/', views.CustomPasswordResetCompleteView.as_view(), name='password_reset_complete'),

    path('about/', views.about, name='about'),
    path('contact/', views.contact, name='contact'),
    path('faq/', views.faq, name='faq'),
    path('privacy/', views.privacy, name='privacy'),
    path('terms/', views.terms, name='terms'),
    path('refund-policy/', views.refund_policy, name='refund_policy'),
    path('newsletter/subscribe/', views.newsletter_subscribe, name='newsletter_subscribe'),
    path('robots.txt', views.robots_txt, name='robots_txt'),
]
