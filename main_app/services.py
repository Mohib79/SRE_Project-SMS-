import json
import logging
import math

import requests
from django.conf import settings
from django.contrib import messages
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse

from .models import Attendance, AttendanceReport, CustomUser, LeaveStatus

logger = logging.getLogger(__name__)


def legacy_json_response(payload, content_type=None):
    """Return a double-encoded JsonResponse matching existing frontend AJAX expectations."""
    kwargs = {"safe": False}
    if content_type is not None:
        kwargs["content_type"] = content_type
    return JsonResponse(json.dumps(payload), **kwargs)


class ProfileImageStorage(FileSystemStorage):
    """Local extension of FileSystemStorage with direct save-and-URL helper."""

    def save_and_get_url(self, uploaded_file):
        if not uploaded_file:
            return None
        filename = self.save(uploaded_file.name, uploaded_file)
        return self.url(filename)


class CaptchaService:
    """Encapsulates Google reCAPTCHA verification outside view handlers."""

    VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"

    @classmethod
    def verify(cls, request):
        if not settings.RECAPTCHA_SECRET_KEY:
            return True, None
        payload = {
            "secret": settings.RECAPTCHA_SECRET_KEY,
            "response": request.POST.get("g-recaptcha-response"),
            "remoteip": request.META.get("REMOTE_ADDR"),
        }
        try:
            captcha_server = requests.post(url=cls.VERIFY_URL, data=payload, timeout=10)
            response = json.loads(captcha_server.text)
            if not response.get("success"):
                return False, "Invalid Captcha. Try Again"
            return True, None
        except Exception:
            return False, "Captcha could not be verified. Try Again"


class UserProfileService:
    """Coordinates CustomUser and role profile creation, updates, and avatar uploads."""

    @classmethod
    def create_user_profile(
        cls, form, uploaded_file, user_type, profile_attr, profile_updates=None
    ):
        data = form.extract_user_data()
        passport_url = ProfileImageStorage().save_and_get_url(uploaded_file) or ""
        with transaction.atomic():
            user = CustomUser.objects.create_user(
                email=data["email"],
                password=data["password"],
                user_type=user_type,
                first_name=data["first_name"],
                last_name=data["last_name"],
                profile_pic=passport_url,
            )
            user.gender = data["gender"]
            user.address = data["address"]
            if profile_updates:
                profile = getattr(user, profile_attr)
                for field, value in profile_updates.items():
                    setattr(profile, field, value)
            user.save()
        return user

    @classmethod
    def update_user_profile(
        cls,
        profile_instance,
        form,
        uploaded_file,
        profile_updates=None,
        update_email=True,
    ):
        data = form.extract_user_data()
        passport_url = ProfileImageStorage().save_and_get_url(uploaded_file)
        user = profile_instance.admin
        with transaction.atomic():
            user.first_name = data["first_name"]
            user.last_name = data["last_name"]
            user.gender = data["gender"]
            user.address = data["address"]
            if update_email:
                user.email = data["email"]
            if data["password"]:
                user.set_password(data["password"])
            if passport_url is not None:
                user.profile_pic = passport_url
            if profile_updates:
                for field, value in profile_updates.items():
                    setattr(profile_instance, field, value)
            user.save()
            profile_instance.save()
        return profile_instance


class NotificationService:
    """Persists in-app notifications and dispatches optional FCM push alerts."""

    FCM_URL = "https://fcm.googleapis.com/fcm/send"

    @classmethod
    def send_fcm(cls, token, message, click_action):
        """Send a push notification through Firebase Cloud Messaging when configured."""
        if not settings.FCM_SERVER_KEY or not token:
            return
        body = {
            "notification": {
                "title": "Student Management System",
                "body": message,
                "click_action": click_action,
                "icon": static("dist/img/AdminLTELogo.png"),
            },
            "to": token,
        }
        headers = {
            "Authorization": "key=" + settings.FCM_SERVER_KEY,
            "Content-Type": "application/json",
        }
        try:
            requests.post(cls.FCM_URL, data=json.dumps(body), headers=headers, timeout=10)
        except requests.RequestException:
            logger.warning("Could not send FCM notification", exc_info=True)

    @classmethod
    def notify_recipient(
        cls, recipient, notification_model, recipient_field, message, click_action_url
    ):
        notification_model.objects.create(
            **{recipient_field: recipient, "message": message}
        )
        cls.send_fcm(recipient.fcm_token, message, click_action_url)


class AttendanceService:
    """Encapsulates attendance persistence and serialization workflows."""

    @staticmethod
    def _parse_student_payload(raw_payload):
        students = json.loads(raw_payload or "")
        if not isinstance(students, list):
            raise ValueError("Expected list of student attendance records")
        return students

    @classmethod
    def save_daily_attendance(cls, subject, session, date, raw_students_json, student_model):
        students = cls._parse_student_payload(raw_students_json)
        with transaction.atomic():
            attendance, _ = Attendance.objects.get_or_create(
                session=session, subject=subject, date=date
            )
            for student_dict in students:
                student = student_model.objects.get(
                    id=student_dict.get("id"), course=subject.course, session=session
                )
                report, created = AttendanceReport.objects.get_or_create(
                    student=student, attendance=attendance
                )
                if created:
                    report.status = bool(int(student_dict.get("status")))
                    report.save()
        return attendance

    @classmethod
    def update_daily_attendance(cls, attendance, raw_students_json):
        students = cls._parse_student_payload(raw_students_json)
        with transaction.atomic():
            for student_dict in students:
                report = AttendanceReport.objects.get(
                    student__admin_id=student_dict.get("id"), attendance=attendance
                )
                report.status = bool(int(student_dict.get("status")))
                report.save()

    @staticmethod
    def serialize_attendance_dates(attendance_qs):
        return [
            {"id": attd.id, "attendance_date": str(attd.date), "session": attd.session_id}
            for attd in attendance_qs
        ]

    @staticmethod
    def serialize_student_reports_for_staff(reports_qs):
        return [
            {
                "id": report.student_user_id,
                "name": report.student_full_name,
                "status": report.status,
            }
            for report in reports_qs
        ]

    @staticmethod
    def serialize_student_reports_for_admin(reports_qs):
        return [
            {"status": str(report.status), "name": str(report.student)}
            for report in reports_qs
        ]


class StudentDashboardBuilder:
    """Method object computing student dashboard summary metrics and breakdowns."""

    def __init__(self, student):
        self.student = student
        self.total_attendance = 0
        self.total_present = 0
        self.percent_present = 0
        self.percent_absent = 0
        self.subjects = []
        self.subject_names = []
        self.data_present = []
        self.data_absent = []

    def _compute_attendance_percentages(self):
        self.total_attendance, self.total_present = self.student.get_attendance_summary()
        if self.total_attendance == 0:
            self.percent_present = 0
            self.percent_absent = 0
            return
        self.percent_present = math.floor(
            (self.total_present / self.total_attendance) * 100
        )
        self.percent_absent = math.ceil(100 - self.percent_present)

    def _build_subject_breakdown(self):
        self.subjects = list(self.student.get_subject_attendance_breakdown())
        for subject in self.subjects:
            self.subject_names.append(subject.name)
            self.data_present.append(subject.present_count)
            self.data_absent.append(subject.absent_count)

    def compute(self):
        self._compute_attendance_percentages()
        self._build_subject_breakdown()
        return {
            "total_attendance": self.total_attendance,
            "percent_present": self.percent_present,
            "percent_absent": self.percent_absent,
            "total_subject": len(self.subjects),
            "subjects": self.subjects,
            "data_present": self.data_present,
            "data_absent": self.data_absent,
            "data_name": self.subject_names,
            "page_title": "Student Homepage",
        }


def handle_simple_model_form(
    request,
    form_class,
    template_name,
    page_title,
    redirect_url_name,
    success_message,
    error_message,
    invalid_message,
    instance=None,
    redirect_args=None,
    extra_context=None,
):
    """Shared CRUD form handler using ModelForm.save() for Course, Subject, and Session."""
    form = form_class(request.POST or None, instance=instance)
    context = {"form": form, "page_title": page_title}
    if extra_context:
        context.update(extra_context)

    if request.method == "POST":
        if form.is_valid():
            try:
                form.save()
                messages.success(request, success_message)
                return redirect(reverse(redirect_url_name, args=redirect_args or []))
            except Exception as exc:
                messages.error(request, error_message.format(error=exc))
        else:
            messages.error(request, invalid_message)
    return render(request, template_name, context)


def handle_leave_application(
    request,
    owner,
    owner_field,
    leave_model,
    form_class,
    template_name,
    redirect_url_name,
    page_title="Apply for Leave",
):
    """Shared leave submission workflow for Staff and Student portals."""
    form = form_class(request.POST or None)
    context = {
        "form": form,
        "leave_history": leave_model.objects.filter(**{owner_field: owner}),
        "page_title": page_title,
    }
    if request.method == "POST":
        if form.is_valid():
            try:
                leave = form.save(commit=False)
                setattr(leave, owner_field, owner)
                leave.save()
                messages.success(
                    request, "Application for leave has been submitted for review"
                )
                return redirect(reverse(redirect_url_name))
            except Exception:
                messages.error(request, "Could not apply!")
        else:
            messages.error(request, "Form has errors!")
    return render(request, template_name, context)


def handle_leave_review(
    request, leave_model, select_related_fields, template_name, page_title
):
    """Shared HOD leave review workflow (list view + AJAX approval/rejection)."""
    if request.method != "POST":
        leaves = leave_model.objects.select_related(*select_related_fields)
        return render(
            request, template_name, {"allLeave": leaves, "page_title": page_title}
        )
    leave_id = request.POST.get("id")
    raw_status = request.POST.get("status")
    try:
        leave = get_object_or_404(leave_model, id=leave_id)
        leave.apply_decision(raw_status)
        return HttpResponse(True)
    except Exception:
        return HttpResponse(False)


def handle_feedback_submission(
    request,
    owner,
    owner_field,
    feedback_model,
    form_class,
    template_name,
    redirect_url_name,
    page_title,
):
    """Shared feedback submission workflow for Staff and Student portals."""
    form = form_class(request.POST or None)
    context = {
        "form": form,
        "feedbacks": feedback_model.objects.filter(**{owner_field: owner}),
        "page_title": page_title,
    }
    if request.method == "POST":
        if form.is_valid():
            try:
                feedback = form.save(commit=False)
                setattr(feedback, owner_field, owner)
                feedback.save()
                messages.success(request, "Feedback submitted for review")
                return redirect(reverse(redirect_url_name))
            except Exception:
                messages.error(request, "Could not Submit!")
        else:
            messages.error(request, "Form has errors!")
    return render(request, template_name, context)


def handle_feedback_reply(
    request, feedback_model, select_related_fields, template_name, page_title
):
    """Shared HOD feedback review workflow (list view + AJAX reply)."""
    if request.method != "POST":
        feedbacks = feedback_model.objects.select_related(*select_related_fields)
        return render(
            request, template_name, {"feedbacks": feedbacks, "page_title": page_title}
        )
    feedback_id = request.POST.get("id")
    reply_text = request.POST.get("reply")
    try:
        feedback = get_object_or_404(feedback_model, id=feedback_id)
        feedback.save_reply(reply_text)
        return HttpResponse(True)
    except Exception:
        return HttpResponse(False)


def handle_fcm_token_update(request):
    """Shared endpoint helper updating the authenticated user's FCM push token."""
    token = request.POST.get("token")
    try:
        user = get_object_or_404(CustomUser, id=request.user.id)
        user.update_fcm_token(token)
        return HttpResponse("True")
    except Exception:
        return HttpResponse("False")
