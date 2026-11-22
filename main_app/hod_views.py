from django.contrib import messages
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import (
    AdminForm,
    CourseForm,
    SessionForm,
    StaffForm,
    StudentForm,
    SubjectForm,
)
from .models import (
    Admin,
    Attendance,
    AttendanceReport,
    Course,
    CustomUser,
    FeedbackStaff,
    FeedbackStudent,
    LeaveReportStaff,
    LeaveReportStudent,
    NotificationStaff,
    NotificationStudent,
    Session,
    Staff,
    Student,
    Subject,
    UserRole,
)
from .services import (
    AttendanceService,
    NotificationService,
    UserProfileService,
    handle_feedback_reply,
    handle_leave_review,
    handle_simple_model_form,
    legacy_json_response,
)


def send_fcm_notification(token, message, click_action):
    """Delegate push notification dispatch to NotificationService."""
    NotificationService.send_fcm(token, message, click_action)


def admin_home(request):
    total_staff = Staff.objects.count()
    total_students = Student.objects.count()
    subjects = list(Subject.objects.annotate(attendance_count=Count("attendance")))
    total_subject = len(subjects)
    total_course = Course.objects.count()
    subject_list = [subject.name[:7] for subject in subjects]
    attendance_list = [subject.attendance_count for subject in subjects]
    context = {
        "page_title": "Administrative Dashboard",
        "total_students": total_students,
        "total_staff": total_staff,
        "total_course": total_course,
        "total_subject": total_subject,
        "subject_list": subject_list,
        "attendance_list": attendance_list,
    }
    return render(request, "hod_template/home_content.html", context)


def add_staff(request):
    form = StaffForm(request.POST or None, request.FILES or None)
    context = {"form": form, "page_title": "Add Staff"}
    if request.method == "POST":
        if form.is_valid():
            try:
                UserProfileService.create_user_profile(
                    form=form,
                    uploaded_file=request.FILES.get("profile_pic"),
                    user_type=UserRole.STAFF,
                    profile_attr="staff",
                    profile_updates={"course": form.cleaned_data.get("course")},
                )
                messages.success(request, "Successfully Added")
                return redirect(reverse("add_staff"))
            except Exception as exc:
                messages.error(request, "Could Not Add " + str(exc))
        else:
            messages.error(request, "Please fulfil all requirements")
    return render(request, "hod_template/add_staff_template.html", context)


def add_student(request):
    student_form = StudentForm(request.POST or None, request.FILES or None)
    context = {"form": student_form, "page_title": "Add Student"}
    if request.method == "POST":
        if student_form.is_valid():
            try:
                UserProfileService.create_user_profile(
                    form=student_form,
                    uploaded_file=request.FILES.get("profile_pic"),
                    user_type=UserRole.STUDENT,
                    profile_attr="student",
                    profile_updates={
                        "course": student_form.cleaned_data.get("course"),
                        "session": student_form.cleaned_data.get("session"),
                    },
                )
                messages.success(request, "Successfully Added")
                return redirect(reverse("add_student"))
            except Exception as exc:
                messages.error(request, "Could Not Add: " + str(exc))
        else:
            messages.error(request, "Could Not Add: ")
    return render(request, "hod_template/add_student_template.html", context)


def add_course(request):
    return handle_simple_model_form(
        request=request,
        form_class=CourseForm,
        template_name="hod_template/add_course_template.html",
        page_title="Add Course",
        redirect_url_name="add_course",
        success_message="Successfully Added",
        error_message="Could Not Add",
        invalid_message="Could Not Add",
    )


def add_subject(request):
    return handle_simple_model_form(
        request=request,
        form_class=SubjectForm,
        template_name="hod_template/add_subject_template.html",
        page_title="Add Subject",
        redirect_url_name="add_subject",
        success_message="Successfully Added",
        error_message="Could Not Add {error}",
        invalid_message="Fill Form Properly",
    )


def manage_staff(request):
    all_staff = CustomUser.objects.filter(user_type=UserRole.STAFF).select_related(
        "staff__course"
    )
    return render(
        request,
        "hod_template/manage_staff.html",
        {"allStaff": all_staff, "page_title": "Manage Staff"},
    )


def manage_student(request):
    students = CustomUser.objects.filter(user_type=UserRole.STUDENT).select_related(
        "student__course"
    )
    return render(
        request,
        "hod_template/manage_student.html",
        {"students": students, "page_title": "Manage Students"},
    )


def manage_course(request):
    courses = Course.objects.all()
    return render(
        request,
        "hod_template/manage_course.html",
        {"courses": courses, "page_title": "Manage Courses"},
    )


def manage_subject(request):
    subjects = Subject.objects.select_related("course", "staff__admin")
    return render(
        request,
        "hod_template/manage_subject.html",
        {"subjects": subjects, "page_title": "Manage Subjects"},
    )


def edit_staff(request, staff_id):
    staff = get_object_or_404(Staff, id=staff_id)
    form = StaffForm(request.POST or None, request.FILES or None, instance=staff)
    context = {"form": form, "staff_id": staff_id, "page_title": "Edit Staff"}
    if request.method == "POST":
        if form.is_valid():
            try:
                UserProfileService.update_user_profile(
                    profile_instance=staff,
                    form=form,
                    uploaded_file=request.FILES.get("profile_pic"),
                    profile_updates={"course": form.cleaned_data.get("course")},
                )
                messages.success(request, "Successfully Updated")
                return redirect(reverse("edit_staff", args=[staff_id]))
            except Exception as exc:
                messages.error(request, "Could Not Update " + str(exc))
        else:
            messages.error(request, "Please fil form properly")
    return render(request, "hod_template/edit_staff_template.html", context)


def edit_student(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    form = StudentForm(request.POST or None, request.FILES or None, instance=student)
    context = {"form": form, "student_id": student_id, "page_title": "Edit Student"}
    if request.method == "POST":
        if form.is_valid():
            try:
                UserProfileService.update_user_profile(
                    profile_instance=student,
                    form=form,
                    uploaded_file=request.FILES.get("profile_pic"),
                    profile_updates={
                        "course": form.cleaned_data.get("course"),
                        "session": form.cleaned_data.get("session"),
                    },
                )
                messages.success(request, "Successfully Updated")
                return redirect(reverse("edit_student", args=[student_id]))
            except Exception as exc:
                messages.error(request, "Could Not Update " + str(exc))
        else:
            messages.error(request, "Please Fill Form Properly!")
    return render(request, "hod_template/edit_student_template.html", context)


def edit_course(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    return handle_simple_model_form(
        request=request,
        form_class=CourseForm,
        template_name="hod_template/edit_course_template.html",
        page_title="Edit Course",
        redirect_url_name="edit_course",
        redirect_args=[course_id],
        success_message="Successfully Updated",
        error_message="Could Not Update",
        invalid_message="Could Not Update",
        instance=course,
        extra_context={"course_id": course_id},
    )


def edit_subject(request, subject_id):
    subject = get_object_or_404(Subject, id=subject_id)
    return handle_simple_model_form(
        request=request,
        form_class=SubjectForm,
        template_name="hod_template/edit_subject_template.html",
        page_title="Edit Subject",
        redirect_url_name="edit_subject",
        redirect_args=[subject_id],
        success_message="Successfully Updated",
        error_message="Could Not Update {error}",
        invalid_message="Fill Form Properly",
        instance=subject,
        extra_context={"subject_id": subject_id},
    )


def add_session(request):
    return handle_simple_model_form(
        request=request,
        form_class=SessionForm,
        template_name="hod_template/add_session_template.html",
        page_title="Add Session",
        redirect_url_name="add_session",
        success_message="Session Created",
        error_message="Could Not Add {error}",
        invalid_message="Fill Form Properly ",
    )


def manage_session(request):
    sessions = Session.objects.all()
    return render(
        request,
        "hod_template/manage_session.html",
        {"sessions": sessions, "page_title": "Manage Sessions"},
    )


def edit_session(request, session_id):
    session = get_object_or_404(Session, id=session_id)
    return handle_simple_model_form(
        request=request,
        form_class=SessionForm,
        template_name="hod_template/edit_session_template.html",
        page_title="Edit Session",
        redirect_url_name="edit_session",
        redirect_args=[session_id],
        success_message="Session Updated",
        error_message="Session Could Not Be Updated {error}",
        invalid_message="Invalid Form Submitted ",
        instance=session,
        extra_context={"session_id": session_id},
    )


@require_POST
def check_email_availability(request):
    email = request.POST.get("email")
    try:
        exists = CustomUser.objects.filter(email=email).exists()
        return HttpResponse(bool(exists))
    except Exception:
        return HttpResponse(False)


def student_feedback_message(request):
    return handle_feedback_reply(
        request=request,
        feedback_model=FeedbackStudent,
        select_related_fields=("student__admin", "student__session"),
        template_name="hod_template/student_feedback_template.html",
        page_title="Student Feedback Messages",
    )


def staff_feedback_message(request):
    return handle_feedback_reply(
        request=request,
        feedback_model=FeedbackStaff,
        select_related_fields=("staff__admin", "staff__course"),
        template_name="hod_template/staff_feedback_template.html",
        page_title="Staff Feedback Messages",
    )


def view_staff_leave(request):
    return handle_leave_review(
        request=request,
        leave_model=LeaveReportStaff,
        select_related_fields=("staff__admin", "staff__course"),
        template_name="hod_template/staff_leave_view.html",
        page_title="Leave Applications From Staff",
    )


def view_student_leave(request):
    return handle_leave_review(
        request=request,
        leave_model=LeaveReportStudent,
        select_related_fields=("student__admin", "student__course"),
        template_name="hod_template/student_leave_view.html",
        page_title="Leave Applications From Students",
    )


def admin_view_attendance(request):
    return render(
        request,
        "hod_template/admin_view_attendance.html",
        {
            "subjects": Subject.objects.all(),
            "sessions": Session.objects.all(),
            "page_title": "View Attendance",
        },
    )


def get_admin_attendance(request):
    subject_id = request.POST.get("subject")
    session_id = request.POST.get("session")
    attendance_date_id = request.POST.get("attendance_date_id")
    try:
        subject = get_object_or_404(Subject, id=subject_id)
        session = get_object_or_404(Session, id=session_id)
        attendance = get_object_or_404(
            Attendance, id=attendance_date_id, session=session, subject=subject
        )
        attendance_reports = AttendanceReport.objects.filter(
            attendance=attendance
        ).select_related("student__admin")
        payload = AttendanceService.serialize_student_reports_for_admin(
            attendance_reports
        )
        return legacy_json_response(payload)
    except Exception:
        return HttpResponse(status=404)


def admin_view_profile(request):
    admin = get_object_or_404(Admin, admin=request.user)
    form = AdminForm(request.POST or None, request.FILES or None, instance=admin)
    context = {"form": form, "page_title": "View/Edit Profile"}
    if request.method == "POST":
        try:
            if form.is_valid():
                UserProfileService.update_user_profile(
                    profile_instance=admin,
                    form=form,
                    uploaded_file=request.FILES.get("profile_pic"),
                    update_email=False,
                )
                messages.success(request, "Profile Updated!")
                return redirect(reverse("admin_view_profile"))
            messages.error(request, "Invalid Data Provided")
        except Exception as exc:
            messages.error(request, "Error Occured While Updating Profile " + str(exc))
    return render(request, "hod_template/admin_view_profile.html", context)


def admin_notify_staff(request):
    staff = CustomUser.objects.filter(user_type=UserRole.STAFF).select_related(
        "staff__course"
    )
    return render(
        request,
        "hod_template/staff_notification.html",
        {"page_title": "Send Notifications To Staff", "allStaff": staff},
    )


def admin_notify_student(request):
    students = CustomUser.objects.filter(user_type=UserRole.STUDENT).select_related(
        "student__course"
    )
    return render(
        request,
        "hod_template/student_notification.html",
        {"page_title": "Send Notifications To Students", "students": students},
    )


@require_POST
def send_student_notification(request):
    admin_id = request.POST.get("id")
    message = request.POST.get("message")
    student = get_object_or_404(
        Student.objects.select_related("admin"), admin_id=admin_id
    )
    try:
        NotificationService.notify_recipient(
            recipient=student,
            notification_model=NotificationStudent,
            recipient_field="student",
            message=message,
            click_action_url=reverse("student_view_notification"),
        )
        return HttpResponse("True")
    except Exception:
        return HttpResponse("False")


@require_POST
def send_staff_notification(request):
    admin_id = request.POST.get("id")
    message = request.POST.get("message")
    staff = get_object_or_404(Staff.objects.select_related("admin"), admin_id=admin_id)
    try:
        NotificationService.notify_recipient(
            recipient=staff,
            notification_model=NotificationStaff,
            recipient_field="staff",
            message=message,
            click_action_url=reverse("staff_view_notification"),
        )
        return HttpResponse("True")
    except Exception:
        return HttpResponse("False")


@require_POST
def delete_staff(request, staff_id):
    staff_user = get_object_or_404(CustomUser, staff__id=staff_id)
    staff_user.delete()
    messages.success(request, "Staff deleted successfully!")
    return redirect(reverse("manage_staff"))


@require_POST
def delete_student(request, student_id):
    student_user = get_object_or_404(CustomUser, student__id=student_id)
    student_user.delete()
    messages.success(request, "Student deleted successfully!")
    return redirect(reverse("manage_student"))


@require_POST
def delete_course(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    try:
        course.delete()
        messages.success(request, "Course deleted successfully!")
    except Exception:
        messages.error(
            request,
            "Sorry, some students are assigned to this course already. Kindly change the affected student course and try again",
        )
    return redirect(reverse("manage_course"))


@require_POST
def delete_subject(request, subject_id):
    subject = get_object_or_404(Subject, id=subject_id)
    subject.delete()
    messages.success(request, "Subject deleted successfully!")
    return redirect(reverse("manage_subject"))


@require_POST
def delete_session(request, session_id):
    session = get_object_or_404(Session, id=session_id)
    try:
        session.delete()
        messages.success(request, "Session deleted successfully!")
    except Exception:
        messages.error(
            request,
            "There are students assigned to this session. Please move them to another session.",
        )
    return redirect(reverse("manage_session"))
