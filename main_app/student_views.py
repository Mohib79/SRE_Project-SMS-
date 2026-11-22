from datetime import datetime

from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import FeedbackStudentForm, LeaveReportStudentForm, StudentEditForm
from .models import (
    Attendance,
    AttendanceReport,
    Course,
    FeedbackStudent,
    LeaveReportStudent,
    NotificationStudent,
    Student,
    StudentResult,
    Subject,
)
from .services import (
    StudentDashboardBuilder,
    UserProfileService,
    handle_fcm_token_update,
    handle_feedback_submission,
    handle_leave_application,
    legacy_json_response,
)


def student_home(request):
    student = get_object_or_404(Student, admin=request.user)
    context = StudentDashboardBuilder(student).compute()
    return render(request, "student_template/home_content.html", context)


def student_view_attendance(request):
    student = get_object_or_404(Student, admin=request.user)
    if request.method != "POST":
        course = get_object_or_404(Course, id=student.course.id)
        return render(
            request,
            "student_template/student_view_attendance.html",
            {"subjects": Subject.objects.filter(course=course), "page_title": "View Attendance"},
        )

    subject_id = request.POST.get("subject")
    start = request.POST.get("start_date")
    end = request.POST.get("end_date")
    try:
        subject = get_object_or_404(Subject, id=subject_id)
        start_date = datetime.strptime(start, "%Y-%m-%d")
        end_date = datetime.strptime(end, "%Y-%m-%d")
        attendance = Attendance.objects.filter(
            date__range=(start_date, end_date), subject=subject
        )
        attendance_reports = AttendanceReport.objects.filter(
            attendance__in=attendance, student=student
        ).select_related("attendance")
        json_data = [
            {"date": str(report.attendance.date), "status": report.status}
            for report in attendance_reports
        ]
        return legacy_json_response(json_data)
    except Exception:
        return HttpResponseBadRequest("Invalid attendance request")


def student_apply_leave(request):
    student = get_object_or_404(Student, admin_id=request.user.id)
    return handle_leave_application(
        request=request,
        owner=student,
        owner_field="student",
        leave_model=LeaveReportStudent,
        form_class=LeaveReportStudentForm,
        template_name="student_template/student_apply_leave.html",
        redirect_url_name="student_apply_leave",
        page_title="Apply for leave",
    )


def student_feedback(request):
    student = get_object_or_404(Student, admin_id=request.user.id)
    return handle_feedback_submission(
        request=request,
        owner=student,
        owner_field="student",
        feedback_model=FeedbackStudent,
        form_class=FeedbackStudentForm,
        template_name="student_template/student_feedback.html",
        redirect_url_name="student_feedback",
        page_title="Student Feedback",
    )


def student_view_profile(request):
    student = get_object_or_404(Student, admin=request.user)
    form = StudentEditForm(request.POST or None, request.FILES or None, instance=student)
    context = {"form": form, "page_title": "View/Edit Profile"}
    if request.method == "POST":
        try:
            if form.is_valid():
                UserProfileService.update_user_profile(
                    profile_instance=student,
                    form=form,
                    uploaded_file=request.FILES.get("profile_pic"),
                    update_email=False,
                )
                messages.success(request, "Profile Updated!")
                return redirect(reverse("student_view_profile"))
            messages.error(request, "Invalid Data Provided")
        except Exception as exc:
            messages.error(request, "Error Occured While Updating Profile " + str(exc))
    return render(request, "student_template/student_view_profile.html", context)


def student_fcmtoken(request):
    return handle_fcm_token_update(request)


def student_view_notification(request):
    student = get_object_or_404(Student, admin=request.user)
    notifications = NotificationStudent.objects.filter(student=student)
    return render(
        request,
        "student_template/student_view_notification.html",
        {"notifications": notifications, "page_title": "View Notifications"},
    )


def student_view_result(request):
    student = get_object_or_404(Student, admin=request.user)
    results = StudentResult.objects.filter(student=student).select_related("subject")
    return render(
        request,
        "student_template/student_view_result.html",
        {"results": results, "page_title": "View Results"},
    )
