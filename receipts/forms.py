from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from .models import UserProfile

User = get_user_model()


def _bootstrap(form):
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.Select):
            widget.attrs.setdefault("class", "form-select")
        elif isinstance(widget, forms.FileInput):
            widget.attrs.setdefault("class", "form-control")
        elif isinstance(widget, forms.CheckboxInput):
            widget.attrs.setdefault("class", "form-check-input")
        else:
            widget.attrs.setdefault("class", "form-control")


class RegisterForm(forms.Form):
    email = forms.EmailField(label="Email")
    nickname = forms.CharField(label="Нікнейм", max_length=64, required=False)
    password1 = forms.CharField(label="Пароль", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Повторіть пароль", widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(self)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Цей email вже зареєстрований.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Паролі не збігаються.")
        if p1:
            validate_password(p1)
        return cleaned

    def save(self):
        email = self.cleaned_data["email"]
        user = User.objects.create_user(
            username=email,
            email=email,
            password=self.cleaned_data["password1"],
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.nickname = self.cleaned_data.get("nickname", "")
        profile.save()
        return user


class LoginForm(forms.Form):
    email = forms.EmailField(label="Email")
    password = forms.CharField(label="Пароль", widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(self)


class ProfileForm(forms.ModelForm):
    email = forms.EmailField(label="Email")

    class Meta:
        model = UserProfile
        fields = ["nickname", "country", "avatar"]
        labels = {
            "nickname": "Нікнейм",
            "country": "Країна",
            "avatar": "Фото профілю",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user_id:
            self.fields["email"].initial = self.instance.user.email
        _bootstrap(self)

    def save(self, commit=True):
        profile = super().save(commit=False)
        email = self.cleaned_data.get("email", "").strip().lower()
        if email:
            user = profile.user
            user.email = email
            user.username = email
            user.save(update_fields=["email", "username"])
        if commit:
            profile.save()
        return profile
