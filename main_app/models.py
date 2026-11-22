from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.db.models import Count, Q
from django.db.models.signals import post_save
from django.dispatch import receiver


class UserRole:
    """Canonical user role codes and dashboard route mappings."""

    HOD = "1"
    STAFF = "2"
    STUDENT = "3"
    CHOICES = ((1, "HOD"), (2, "Staff"), (3, "Student"))
    DASHBOARD_URLS = {
        HOD: "admin_home",
        STAFF: "staff_home",
        STUDENT: "student_home",
    }

    @classmethod
    def normalize(cls, value):
        return str(value)


class LeaveStatus:
    """Domain constants and parser for leave request workflow states."""

    PENDING = 0
    APPROVED = 1
    REJECTED = -1

    @classmethod
    def from_raw(cls, raw_status):
        return cls.APPROVED if str(raw_status) == "1" else cls.REJECTED


class TimeStampedModel(models.Model):
    """Abstract base model providing creation and modification timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UserProfileDelegateMixin:
    """Hides delegation across profile -> CustomUser chains."""

    @property
    def email(self):
        return self.admin.email

    @property
    def fcm_token(self):
        return self.admin.fcm_token

    @property
    def full_name(self):
        return self.admin.get_full_name()

    @property
    def last_name(self):
        return self.admin.last_name

    @property
    def user_id(self):
        return self.admin_id


class CustomUserManager(UserManager):
    def _create_user(self, email, password, **extra_fields):
        email = self.normalize_email(email)
        user = CustomUser(email=email, **extra_fields)
        user.password = make_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        assert extra_fields["is_staff"]
        assert extra_fields["is_superuser"]
        return self._create_user(email, password, **extra_fields)


class Session(models.Model):
    start_year = models.DateField()
    end_year = models.DateField()

    def __str__(self):
        return "From " + str(self.start_year) + " to " + str(self.end_year)


class CustomUser(AbstractUser, TimeStampedModel):
    USER_TYPE = UserRole.CHOICES
    GENDER = [("M", "Male"), ("F", "Female")]

    username = None  # Removed username, using email instead
    email = models.EmailField(unique=True)
    user_type = models.CharField(default=1, choices=USER_TYPE, max_length=1)
    gender = models.CharField(max_length=1, choices=GENDER)
    profile_pic = models.ImageField()
    address = models.TextField()
    fcm_token = models.TextField(default="")  # For firebase notifications
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = CustomUserManager()

    def __str__(self):
        return self.last_name + ", " + self.first_name

    def get_full_name(self):
        return f"{self.last_name} {self.first_name}".strip()

    def get_dashboard_url_name(self):
        return UserRole.DASHBOARD_URLS.get(UserRole.normalize(self.user_type))

    def update_fcm_token(self, token):
        self.fcm_token = token
        self.save(update_fields=["fcm_token", "updated_at"])


class Admin(UserProfileDelegateMixin, models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)


class Course(TimeStampedModel):
    name = models.CharField(max_length=120)

    def __str__(self):
        return self.name


class Student(UserProfileDelegateMixin, models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.DO_NOTHING, null=True, blank=False)
    session = models.ForeignKey(Session, on_delete=models.DO_NOTHING, null=True)

    def __str__(self):
        return self.admin.last_name + ", " + self.admin.first_name

    def get_attendance_summary(self):
        """Return (total_records, present_records) for this student."""
        totals = AttendanceReport.objects.filter(student=self).aggregate(
            total=Count("id"),
            present=Count("id", filter=Q(status=True)),
        )
        return totals["total"], totals["present"]

    def get_subject_attendance_breakdown(self):
        """Return subjects in this student's course annotated with present/absent counts."""
        return Subject.objects.filter(course=self.course).annotate(
            present_count=Count(
                "attendance__attendancereport",
                filter=Q(
                    attendance__attendancereport__student=self,
                    attendance__attendancereport__status=True,
                ),
            ),
            absent_count=Count(
                "attendance__attendancereport",
                filter=Q(
                    attendance__attendancereport__student=self,
                    attendance__attendancereport__status=False,
                ),
            ),
        )


class Staff(UserProfileDelegateMixin, models.Model):
    course = models.ForeignKey(Course, on_delete=models.DO_NOTHING, null=True, blank=False)
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)

    def __str__(self):
        return self.admin.last_name + " " + self.admin.first_name

    def teaches_subject(self, subject_id):
        return Subject.objects.filter(id=subject_id, staff=self).exists()

    def get_subjects_with_attendance_counts(self):
        return list(Subject.objects.filter(staff=self).annotate(attendance_count=Count("attendance")))


class Subject(TimeStampedModel):
    name = models.CharField(max_length=120)
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)

    def __str__(self):
        return self.name


class Attendance(TimeStampedModel):
    session = models.ForeignKey(Session, on_delete=models.DO_NOTHING)
    subject = models.ForeignKey(Subject, on_delete=models.DO_NOTHING)
    date = models.DateField()


class AttendanceReport(TimeStampedModel):
    student = models.ForeignKey(Student, on_delete=models.DO_NOTHING)
    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE)
    status = models.BooleanField(default=False)

    @property
    def student_user_id(self):
        return self.student.user_id

    @property
    def student_full_name(self):
        return self.student.full_name


class BaseLeaveReport(TimeStampedModel):
    """Abstract base model shared by staff and student leave applications."""

    date = models.CharField(max_length=60)
    message = models.TextField()
    status = models.SmallIntegerField(default=LeaveStatus.PENDING)

    class Meta:
        abstract = True

    def apply_decision(self, raw_status):
        decision_status = LeaveStatus.from_raw(raw_status)
        self.status = decision_status
        self.save(update_fields=["status", "updated_at"])
        return self


class LeaveReportStudent(BaseLeaveReport):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)


class LeaveReportStaff(BaseLeaveReport):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)


class BaseFeedback(TimeStampedModel):
    """Abstract base model shared by staff and student feedback threads."""

    feedback = models.TextField()
    reply = models.TextField()

    class Meta:
        abstract = True

    def save_reply(self, reply_text):
        self.reply = reply_text
        self.save(update_fields=["reply", "updated_at"])
        return self


class FeedbackStudent(BaseFeedback):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)


class FeedbackStaff(BaseFeedback):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)


class BaseNotification(TimeStampedModel):
    """Abstract base model shared by staff and student notifications."""

    message = models.TextField()

    class Meta:
        abstract = True


class NotificationStaff(BaseNotification):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)


class NotificationStudent(BaseNotification):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)


class StudentResult(TimeStampedModel):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    test = models.FloatField(default=0)
    exam = models.FloatField(default=0)

    @classmethod
    def save_or_update_score(cls, student, subject, test_score, exam_score):
        result, created = cls.objects.get_or_create(
            student=student,
            subject=subject,
            defaults={"test": test_score, "exam": exam_score},
        )
        if not created:
            result.test = test_score
            result.exam = exam_score
            result.save(update_fields=["test", "exam", "updated_at"])
        return result, created


PROFILE_MODEL_BY_ROLE = {
    UserRole.HOD: Admin,
    UserRole.STAFF: Staff,
    UserRole.STUDENT: Student,
}

PROFILE_ATTR_BY_ROLE = {
    UserRole.HOD: "admin",
    UserRole.STAFF: "staff",
    UserRole.STUDENT: "student",
}


@receiver(post_save, sender=CustomUser)
def create_user_profile(sender, instance, created, **kwargs):
    if not created:
        return
    profile_model = PROFILE_MODEL_BY_ROLE.get(UserRole.normalize(instance.user_type))
    if profile_model is not None:
        profile_model.objects.create(admin=instance)


@receiver(post_save, sender=CustomUser)
def save_user_profile(sender, instance, **kwargs):
    attr = PROFILE_ATTR_BY_ROLE.get(UserRole.normalize(instance.user_type))
    if attr and hasattr(instance, attr):
        getattr(instance, attr).save()
