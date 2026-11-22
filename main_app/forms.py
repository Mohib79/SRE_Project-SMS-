from django import forms
from django.forms.widgets import DateInput

from .models import (
    Admin,
    Course,
    CustomUser,
    FeedbackStaff,
    FeedbackStudent,
    LeaveReportStaff,
    LeaveReportStudent,
    Session,
    Staff,
    Student,
    StudentResult,
    Subject,
)


class FormSettings(forms.ModelForm):
    """Base ModelForm applying Bootstrap form-control styling to visible fields."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.visible_fields():
            field.field.widget.attrs["class"] = "form-control"


class CustomUserForm(FormSettings):
    """Shared user account fields, email uniqueness check, and profile hydration."""

    email = forms.EmailField(required=True)
    gender = forms.ChoiceField(choices=[("M", "Male"), ("F", "Female")])
    first_name = forms.CharField(required=True)
    last_name = forms.CharField(required=True)
    address = forms.CharField(widget=forms.Textarea)
    password = forms.CharField(widget=forms.PasswordInput)
    profile_pic = forms.ImageField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk is not None:
            self._populate_user_initial_fields(self.instance)

    def _populate_user_initial_fields(self, profile_instance):
        user = getattr(profile_instance, "admin", profile_instance)
        self.fields["password"].required = False
        self.fields["profile_pic"].required = False
        self.fields["password"].widget.attrs["placeholder"] = (
            "Fill this only if you wish to update password"
        )
        for field in CustomUserForm.Meta.fields:
            self.fields[field].initial = getattr(user, field, None)

    def _get_existing_email(self):
        if hasattr(self.instance, "admin"):
            return self.instance.email.lower()
        return (
            CustomUser.objects.values_list("email", flat=True)
            .get(pk=self.instance.pk)
            .lower()
        )

    def clean_email(self):
        form_email = self.cleaned_data["email"].lower()
        is_insert = self.instance.pk is None
        if is_insert:
            if CustomUser.objects.filter(email=form_email).exists():
                raise forms.ValidationError("The given email is already registered")
            return form_email

        if self._get_existing_email() != form_email:
            if CustomUser.objects.filter(email=form_email).exists():
                raise forms.ValidationError("The given email is already registered")
        return form_email

    def extract_user_data(self):
        """Return cleaned user fields as a single dict for profile services."""
        return {
            "first_name": self.cleaned_data.get("first_name"),
            "last_name": self.cleaned_data.get("last_name"),
            "email": self.cleaned_data.get("email"),
            "gender": self.cleaned_data.get("gender"),
            "address": self.cleaned_data.get("address"),
            "password": self.cleaned_data.get("password") or None,
        }

    class Meta:
        model = CustomUser
        fields = [
            "first_name",
            "last_name",
            "email",
            "gender",
            "password",
            "profile_pic",
            "address",
        ]


class ProfileEditForm(CustomUserForm):
    """Shared base for self-service profile editing forms without role-only fields."""

    class Meta(CustomUserForm.Meta):
        fields = CustomUserForm.Meta.fields


class StudentForm(CustomUserForm):
    class Meta(CustomUserForm.Meta):
        model = Student
        fields = CustomUserForm.Meta.fields + ["course", "session"]


class AdminForm(ProfileEditForm):
    class Meta(ProfileEditForm.Meta):
        model = Admin


class StaffForm(CustomUserForm):
    class Meta(CustomUserForm.Meta):
        model = Staff
        fields = CustomUserForm.Meta.fields + ["course"]


class StudentEditForm(ProfileEditForm):
    class Meta(ProfileEditForm.Meta):
        model = Student


class StaffEditForm(ProfileEditForm):
    class Meta(ProfileEditForm.Meta):
        model = Staff


class CourseForm(FormSettings):
    class Meta:
        model = Course
        fields = ["name"]


class SubjectForm(FormSettings):
    class Meta:
        model = Subject
        fields = ["name", "staff", "course"]


class SessionForm(FormSettings):
    class Meta:
        model = Session
        fields = "__all__"
        widgets = {
            "start_year": DateInput(attrs={"type": "date"}),
            "end_year": DateInput(attrs={"type": "date"}),
        }


class BaseLeaveReportForm(FormSettings):
    class Meta:
        fields = ["date", "message"]
        widgets = {
            "date": DateInput(attrs={"type": "date"}),
        }


class LeaveReportStaffForm(BaseLeaveReportForm):
    class Meta(BaseLeaveReportForm.Meta):
        model = LeaveReportStaff


class LeaveReportStudentForm(BaseLeaveReportForm):
    class Meta(BaseLeaveReportForm.Meta):
        model = LeaveReportStudent


class BaseFeedbackForm(FormSettings):
    class Meta:
        fields = ["feedback"]


class FeedbackStaffForm(BaseFeedbackForm):
    class Meta(BaseFeedbackForm.Meta):
        model = FeedbackStaff


class FeedbackStudentForm(BaseFeedbackForm):
    class Meta(BaseFeedbackForm.Meta):
        model = FeedbackStudent


class EditResultForm(FormSettings):
    session_year = forms.ModelChoiceField(
        label="Session Year", queryset=Session.objects.all(), required=True
    )

    class Meta:
        model = StudentResult
        fields = ["session_year", "subject", "student", "test", "exam"]
