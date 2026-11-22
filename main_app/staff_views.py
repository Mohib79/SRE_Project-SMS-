import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.decorators.http import require_POST

from .forms import (
    EditResultForm,
    FeedbackStaffForm,
    LeaveReportStaffForm,
    StaffEditForm,
)
from .models import (
    Attendance,
    AttendanceReport,
    FeedbackStaff,
    LeaveReportStaff,
    NotificationStaff,
    Session,
    Staff,
    Student,
    StudentResult,
    Subject,
    UserRole,
)
from .services import (
    AttendanceService,
    UserProfileService,
    handle_fcm_token_update,
    handle_feedback_submission,
    handle_leave_application,
    legacy_json_response,
)

# Maximum scores accepted for a result (mirrors the limits used in the UI).
MAX_TEST_SCORE = 40
MAX_EXAM_SCORE = 60


def get_current_staff(request):
    """Return the Staff record of the logged in user, or deny access."""
    role = UserRole.normalize(getattr(request.user, "user_type", ""))
    if not request.user.is_authenticated or role != UserRole.STAFF:
        raise PermissionDenied
    return get_object_or_404(Staff, admin=request.user)


def get_staff_subject(staff, subject_id):
    """Return a subject only if it is taught by ``staff``."""
    return get_object_or_404(Subject, id=subject_id, staff=staff)


def parse_scores(test, exam):
    """Validate and convert test / exam scores, raising ValueError if invalid."""
    test_score = float(test)
    exam_score = float(exam)
    if not (0 <= test_score <= MAX_TEST_SCORE) or not (0 <= exam_score <= MAX_EXAM_SCORE):
        raise ValueError("Score out of range")
    return test_score, exam_score


def staff_home(request):
    staff = get_object_or_404(
        Staff.objects.select_related("admin", "course"), admin=request.user
    )
    total_students = Student.objects.filter(course=staff.course).count()
    total_leave = LeaveReportStaff.objects.filter(staff=staff).count()
    subjects = staff.get_subjects_with_attendance_counts()
    subject_list = [subject.name for subject in subjects]
    attendance_list = [subject.attendance_count for subject in subjects]
    context = {
        "page_title": f"Staff Panel - {staff.last_name} ({staff.course})",
        "total_students": total_students,
        "total_attendance": sum(attendance_list),
        "total_leave": total_leave,
        "total_subject": len(subjects),
        "subject_list": subject_list,
        "attendance_list": attendance_list,
    }
    return render(request, "staff_template/home_content.html", context)


def staff_take_attendance(request):
    staff = get_object_or_404(Staff, admin=request.user)
    return render(
        request,
        "staff_template/staff_take_attendance.html",
        {
            "subjects": Subject.objects.filter(staff_id=staff),
            "sessions": Session.objects.all(),
            "page_title": "Take Attendance",
        },
    )


@require_POST
def get_students(request):
    staff = get_current_staff(request)
    subject = get_staff_subject(staff, request.POST.get("subject"))
    session = get_object_or_404(Session, id=request.POST.get("session"))
    students = Student.objects.filter(
        course_id=subject.course_id, session=session
    ).select_related("admin")
    student_data = [
        {"id": student.id, "name": student.full_name} for student in students
    ]
    return legacy_json_response(student_data, content_type="application/json")


@require_POST
def save_attendance(request):
    staff = get_current_staff(request)
    subject = get_staff_subject(staff, request.POST.get("subject"))
    session = get_object_or_404(Session, id=request.POST.get("session"))
    date = request.POST.get("date")
    try:
        AttendanceService.save_daily_attendance(
            subject=subject,
            session=session,
            date=date,
            raw_students_json=request.POST.get("student_ids"),
            student_model=Student,
        )
    except Exception:
        return HttpResponseBadRequest("Invalid attendance data")
    return HttpResponse("OK")


def staff_update_attendance(request):
    staff = get_object_or_404(Staff, admin=request.user)
    return render(
        request,
        "staff_template/staff_update_attendance.html",
        {
            "subjects": Subject.objects.filter(staff_id=staff),
            "sessions": Session.objects.all(),
            "page_title": "Update Attendance",
        },
    )


@require_POST
def get_student_attendance(request):
    staff = get_current_staff(request)
    attendance = get_object_or_404(
        Attendance, id=request.POST.get("attendance_date_id"), subject__staff=staff
    )
    attendance_data = AttendanceReport.objects.filter(
        attendance=attendance
    ).select_related("student__admin")
    student_data = AttendanceService.serialize_student_reports_for_staff(
        attendance_data
    )
    return legacy_json_response(student_data, content_type="application/json")


@require_POST
def update_attendance(request):
    staff = get_current_staff(request)
    attendance = get_object_or_404(
        Attendance, id=request.POST.get("date"), subject__staff=staff
    )
    try:
        AttendanceService.update_daily_attendance(
            attendance=attendance,
            raw_students_json=request.POST.get("student_ids"),
        )
    except Exception:
        return HttpResponseBadRequest("Invalid attendance data")
    return HttpResponse("OK")


def staff_apply_leave(request):
    staff = get_object_or_404(Staff, admin_id=request.user.id)
    return handle_leave_application(
        request=request,
        owner=staff,
        owner_field="staff",
        leave_model=LeaveReportStaff,
        form_class=LeaveReportStaffForm,
        template_name="staff_template/staff_apply_leave.html",
        redirect_url_name="staff_apply_leave",
        page_title="Apply for Leave",
    )


def staff_feedback(request):
    staff = get_object_or_404(Staff, admin_id=request.user.id)
    return handle_feedback_submission(
        request=request,
        owner=staff,
        owner_field="staff",
        feedback_model=FeedbackStaff,
        form_class=FeedbackStaffForm,
        template_name="staff_template/staff_feedback.html",
        redirect_url_name="staff_feedback",
        page_title="Add Feedback",
    )


def staff_view_profile(request):
    staff = get_object_or_404(Staff, admin=request.user)
    form = StaffEditForm(request.POST or None, request.FILES or None, instance=staff)
    context = {"form": form, "page_title": "View/Update Profile"}
    if request.method == "POST":
        try:
            if form.is_valid():
                UserProfileService.update_user_profile(
                    profile_instance=staff,
                    form=form,
                    uploaded_file=request.FILES.get("profile_pic"),
                    update_email=False,
                )
                messages.success(request, "Profile Updated!")
                return redirect(reverse("staff_view_profile"))
            messages.error(request, "Invalid Data Provided")
        except Exception as exc:
            messages.error(request, "Error Occured While Updating Profile " + str(exc))
    return render(request, "staff_template/staff_view_profile.html", context)


def staff_fcmtoken(request):
    return handle_fcm_token_update(request)


def staff_view_notification(request):
    staff = get_object_or_404(Staff, admin=request.user)
    notifications = NotificationStaff.objects.filter(staff=staff)
    return render(
        request,
        "staff_template/staff_view_notification.html",
        {"notifications": notifications, "page_title": "View Notifications"},
    )


def staff_add_result(request):
    staff = get_current_staff(request)
    context = {
        "page_title": "Result Upload",
        "subjects": Subject.objects.filter(staff=staff),
        "sessions": Session.objects.all(),
    }
    if request.method == "POST":
        try:
            student_id = request.POST.get("student_list")
            subject_id = request.POST.get("subject")
            test_score, exam_score = parse_scores(
                request.POST.get("test"), request.POST.get("exam")
            )
            subject = Subject.objects.get(id=subject_id, staff=staff)
            student = Student.objects.get(id=student_id, course=subject.course)
            _, created = StudentResult.save_or_update_score(
                student=student,
                subject=subject,
                test_score=test_score,
                exam_score=exam_score,
            )
            messages.success(request, "Scores Saved" if created else "Scores Updated")
        except Exception:
            messages.warning(request, "Error Occured While Processing Form")
    return render(request, "staff_template/staff_add_result.html", context)


@require_POST
def fetch_student_result(request):
    staff = get_current_staff(request)
    try:
        subject = Subject.objects.get(id=request.POST.get("subject"), staff=staff)
        student = Student.objects.get(
            id=request.POST.get("student"), course=subject.course
        )
        result = StudentResult.objects.get(student=student, subject=subject)
        return HttpResponse(json.dumps({"exam": result.exam, "test": result.test}))
    except Exception:
        return HttpResponse("False")


class EditResultView(View):
    """Lets a staff member edit results for subjects they teach only."""

    def dispatch(self, request, *args, **kwargs):
        self.staff = get_current_staff(request)
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, data=None):
        form = EditResultForm(data)
        subjects = Subject.objects.filter(staff=self.staff)
        form.fields["subject"].queryset = subjects
        form.fields["student"].queryset = Student.objects.filter(
            course__in=subjects.values("course")
        )
        return form

    def get(self, request, *args, **kwargs):
        return render(
            request,
            "staff_template/edit_student_result.html",
            {"form": self.get_form(), "page_title": "Edit Student's Result"},
        )

    def post(self, request, *args, **kwargs):
        form = self.get_form(request.POST)
        context = {"form": form, "page_title": "Edit Student's Result"}
        if form.is_valid():
            try:
                student = form.cleaned_data.get("student")
                subject = form.cleaned_data.get("subject")
                test_score, exam_score = parse_scores(
                    form.cleaned_data.get("test"), form.cleaned_data.get("exam")
                )
                teaches_subject = subject.staff_id == self.staff.id
                student_enrolled = student.course_id == subject.course_id
                if not (teaches_subject and student_enrolled):
                    raise PermissionDenied
                result = StudentResult.objects.get(student=student, subject=subject)
                result.exam = exam_score
                result.test = test_score
                result.save()
                messages.success(request, "Result Updated")
                return redirect(reverse("edit_student_result"))
            except Exception:
                messages.warning(request, "Result Could Not Be Updated")
        else:
            messages.warning(request, "Result Could Not Be Updated")
        return render(request, "staff_template/edit_student_result.html", context)
