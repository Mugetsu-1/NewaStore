from django import forms
from django.contrib.auth.forms import UserCreationForm, UserChangeForm, PasswordResetForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from .payments import _available_payment_method_choices
from .models import (
    Product, ProductImage, ProductVariant, Review, Category, Tag,
    Coupon, Cart, CartItem, Wishlist, WishlistItem,
    Order, OrderItem, NewsletterSubscriber, ContactMessage, SiteSettings
)


class CustomRegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=30, required=True)
    last_name = forms.CharField(max_length=30, required=True)
    phone = forms.CharField(max_length=20, required=False)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'password1', 'password2']

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        
        if User.objects.filter(email=email).exists():
            raise ValidationError("This email is already registered.")
        
        return email

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip().lower()
        
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("This username is already taken.")
        
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        if commit:
            user.save()
        return user


class UserProfileForm(forms.ModelForm):
    phone = forms.CharField(max_length=20, required=False)
    date_of_birth = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if User.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
            raise ValidationError("This email is already in use.")
        return email


class UserProfileUpdateForm(UserProfileForm):
    class Meta(UserProfileForm.Meta):
        fields = ['first_name', 'last_name', 'email']


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'slug', 'description', 'image', 'parent', 'is_active', 'sort_order']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ['name', 'slug']


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            'name', 'slug', 'sku', 'barcode', 'category', 'tags',
            'short_description', 'description',
            'price', 'discount_price', 'cost_price',
            'stock_quantity', 'low_stock_threshold', 'track_inventory', 'allow_backorder',
            'is_active', 'is_featured', 'is_digital',
            'meta_title', 'meta_description', 'meta_keywords',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5}),
            'short_description': forms.Textarea(attrs={'rows': 3}),
            'meta_description': forms.Textarea(attrs={'rows': 3}),
            'meta_keywords': forms.Textarea(attrs={'rows': 2}),
            'tags': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tags'].queryset = Tag.objects.all()
        self.fields['category'].queryset = Category.objects.filter(is_active=True)


class ProductImageForm(forms.ModelForm):
    class Meta:
        model = ProductImage
        fields = ['image', 'alt_text', 'is_primary', 'sort_order']


ProductImageFormSet = forms.inlineformset_factory(
    Product, ProductImage,
    form=ProductImageForm,
    extra=1,
    can_delete=True
)


class ProductVariantForm(forms.ModelForm):
    class Meta:
        model = ProductVariant
        fields = ['name', 'sku', 'price_adjustment', 'stock_quantity', 'is_active', 'attributes', 'sort_order']
        widgets = {
            'attributes': forms.Textarea(attrs={'rows': 3, 'placeholder': '{"color": "Red", "size": "M"}'}),
        }


ProductVariantFormSet = forms.inlineformset_factory(
    Product, ProductVariant,
    form=ProductVariantForm,
    extra=1,
    can_delete=True
)


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['rating', 'title', 'comment']
        widgets = {
            'rating': forms.Select(choices=[(i, f'{i} Star{"s" if i > 1 else ""}') for i in range(1, 6)]),
            'comment': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Share your experience with this product...'}),
            'title': forms.TextInput(attrs={'placeholder': 'Summary of your review (optional)'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        self.product = kwargs.pop('product', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        if self.user and self.product:
            if Review.objects.filter(user=self.user, product=self.product).exclude(pk=self.instance.pk).exists():
                raise ValidationError("You have already reviewed this product.")
        return cleaned_data


class CouponForm(forms.ModelForm):
    class Meta:
        model = Coupon
        fields = [
            'code', 'name', 'description', 'discount_type', 'discount_value',
            'minimum_amount', 'maximum_discount',
            'usage_limit', 'usage_limit_per_user',
            'valid_from', 'valid_until', 'is_active',
            'applicable_categories', 'applicable_products', 'excluded_products',
        ]
        widgets = {
            'valid_from': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'valid_until': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'description': forms.Textarea(attrs={'rows': 3}),
            'applicable_categories': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['applicable_categories'].queryset = Category.objects.filter(is_active=True)
        for field_name in ('applicable_products', 'excluded_products'):
            initial_ids = list(self.initial.get(field_name, []) or [])
            self.fields[field_name] = forms.CharField(
                required=False,
                label=self.fields[field_name].label,
                help_text='Comma-separated product names or IDs',
                initial=', '.join(str(p) for p in
                                  Product.objects.filter(id__in=initial_ids)
                                  .values_list('name', flat=True)) if initial_ids else '',
            )

    def _clean_product_picker(self, field_name):
        raw = self.cleaned_data.get(field_name, '') or ''
        picked = []
        for token in [t.strip() for t in raw.split(',') if t.strip()]:
            if token.isdigit():
                product = Product.objects.filter(id=int(token)).first()
            else:
                product = Product.objects.filter(name__iexact=token).first()
            if product:
                picked.append(product)
            else:
                raise ValidationError(f'No product matches "{token}".')
        return picked

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data['applicable_products'] = self._clean_product_picker('applicable_products')
        cleaned_data['excluded_products'] = self._clean_product_picker('excluded_products')
        return cleaned_data


class CartItemForm(forms.ModelForm):
    class Meta:
        model = CartItem
        fields = ['quantity']
        widgets = {
            'quantity': forms.NumberInput(attrs={'min': 1, 'max': 99, 'class': 'quantity-input'}),
        }


class CouponApplyForm(forms.Form):
    code = forms.CharField(max_length=50, widget=forms.TextInput(attrs={
        'placeholder': 'Enter coupon code',
        'class': 'coupon-input'
    }))


class CheckoutForm(forms.Form):
    billing_full_name = forms.CharField(max_length=100, label='Full Name')
    billing_phone = forms.CharField(max_length=20, label='Phone Number')
    billing_email = forms.EmailField(label='Email Address')
    billing_address_line_1 = forms.CharField(max_length=200, label='Address Line 1')
    billing_address_line_2 = forms.CharField(max_length=200, required=False, label='Address Line 2 (Optional)')
    billing_city = forms.CharField(max_length=100, label='City')
    billing_state = forms.CharField(max_length=100, label='State/Province')
    billing_postal_code = forms.CharField(max_length=20, label='Postal Code')
    billing_country = forms.CharField(max_length=100, initial='Nepal', label='Country')

    PAYMENT_CHOICES = [
        ('esewa', 'eSewa (Simulated)'),
        ('khalti', 'Khalti (Simulated)'),
        ('nay_bank', 'Nay Bank Transfer'),
        ('stripe', 'Credit/Debit Card (Stripe)'),
        ('paypal', 'PayPal'),
    ]
    payment_method = forms.ChoiceField(choices=PAYMENT_CHOICES, widget=forms.RadioSelect, initial='esewa')

    order_notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False, label='Order Notes')
    terms_accepted = forms.BooleanField(required=True, label='I agree to the Terms & Conditions')

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        self.cart = kwargs.pop('cart', None)
        super().__init__(*args, **kwargs)
        self.fields['payment_method'].choices = _available_payment_method_choices()

        if self.user and getattr(self.user, 'is_authenticated', False):
            if self.user.email:
                self.fields['billing_email'].initial = self.user.email
            full_name = (self.user.get_full_name() or '').strip()
            if full_name:
                self.fields['billing_full_name'].initial = full_name


class OrderStatusUpdateForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['status', 'payment_status', 'internal_notes']
        widgets = {
            'internal_notes': forms.Textarea(attrs={'rows': 3}),
        }


class NewsletterForm(forms.ModelForm):
    class Meta:
        model = NewsletterSubscriber
        fields = ['email']
        widgets = {
            'email': forms.EmailInput(attrs={'placeholder': 'Enter your email address', 'class': 'newsletter-input'}),
        }

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if NewsletterSubscriber.objects.filter(email=email, is_active=True).exists():
            raise ValidationError("This email is already subscribed.")
        return email


class ContactForm(forms.ModelForm):
    class Meta:
        model = ContactMessage
        fields = ['name', 'email', 'subject', 'message']
        widgets = {
            'message': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Your message...'}),
            'subject': forms.TextInput(attrs={'placeholder': 'Subject'}),
        }


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = '__all__'
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
            'maintenance_message': forms.Textarea(attrs={'rows': 3}),
            'meta_description': forms.Textarea(attrs={'rows': 3}),
            'meta_keywords': forms.Textarea(attrs={'rows': 2}),
        }


class ProductSearchForm(forms.Form):
    q = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={
        'placeholder': 'Search products...',
        'class': 'search-input'
    }))
    category = forms.ModelChoiceField(
        queryset=Category.objects.filter(is_active=True, parent__isnull=True),
        required=False,
        empty_label='All Categories'
    )
    min_price = forms.DecimalField(max_digits=10, decimal_places=2, required=False, widget=forms.NumberInput(attrs={
        'placeholder': 'Min Price',
        'step': '0.01',
        'min': '0'
    }))
    max_price = forms.DecimalField(max_digits=10, decimal_places=2, required=False, widget=forms.NumberInput(attrs={
        'placeholder': 'Max Price',
        'step': '0.01',
        'min': '0'
    }))
    sort_by = forms.ChoiceField(
        choices=[
            ('-created_at', 'Newest'),
            ('name', 'Name A-Z'),
            ('-name', 'Name Z-A'),
            ('price', 'Price Low to High'),
            ('-price', 'Price High to Low'),
            ('-is_featured', 'Featured First'),
            ('rating', 'Top Rated'),
        ],
        required=False,
        initial='-created_at'
    )
    in_stock_only = forms.BooleanField(required=False, label='In Stock Only')
    on_sale_only = forms.BooleanField(required=False, label='On Sale Only')