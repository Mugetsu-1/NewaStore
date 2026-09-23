from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from .payments import _available_payment_method_choices
from .models import Review, Category, NewsletterSubscriber, ContactMessage, SavedBillingDetail


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
        ('esewa', 'eSewa'),
        ('nay_bank', 'Nay Bank Transfer'),
    ]
    payment_method = forms.ChoiceField(choices=PAYMENT_CHOICES, widget=forms.RadioSelect, initial='esewa')

    order_notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False, label='Order Notes')
    terms_accepted = forms.BooleanField(required=True, label='I agree to the Terms & Conditions')
    save_details = forms.BooleanField(
        required=False,
        initial=True,
        label='Save these billing details for faster checkout next time',
    )

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

            saved = SavedBillingDetail.objects.filter(user=self.user).first()
            if saved:
                prefill = {
                    'billing_full_name': saved.full_name,
                    'billing_phone': saved.phone,
                    'billing_email': saved.email,
                    'billing_address_line_1': saved.address_line_1,
                    'billing_address_line_2': saved.address_line_2,
                    'billing_city': saved.city,
                    'billing_state': saved.state,
                    'billing_postal_code': saved.postal_code,
                    'billing_country': saved.country,
                }
                for field, value in prefill.items():
                    if value:
                        self.fields[field].initial = value


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
            ('featured', 'Recommended'),
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