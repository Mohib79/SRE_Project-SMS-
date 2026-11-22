import datetime
import json

from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import (Attendance, AttendanceReport, Course, CustomUser,
                     Session, Staff, Student, StudentResult, Subject)


def CaptureQueries():
    return CaptureQueriesContext(connection)


def make_user(email, user_type):
    user = CustomUser.objects.create_user(
        email=email, password='Passw0rd!', user_type=user_type,
        first_name='First', last_name='Last', gender='M', address='addr')
    user.refresh_from_db()
    return user


class AuthorizationTests(TestCase):
    """Regression tests for broken access control on staff endpoints."""

    def setUp(self):
        self.session = Session.objects.create(
            start_year=datetime.date(2026, 1, 1), end_year=datetime.date(2026, 12, 31))
        self.course_a = Course.objects.create(name='CSE')
        self.course_b = Course.objects.create(name='ECE')

        self.teacher_a = make_user('staff.a@example.com', 2)
        self.teacher_b = make_user('staff.b@example.com', 2)
        self.staff_a = Staff.objects.get(admin=self.teacher_a)
        self.staff_b = Staff.objects.get(admin=self.teacher_b)
        self.subject_a = Subject.objects.create(
            name='CSE-101', staff=self.staff_a, course=self.course_a)
        self.subject_b = Subject.objects.create(
            name='ECE-201', staff=self.staff_b, course=self.course_b)

        self.student_user = make_user('stu.a@example.com', 3)
        self.student = Student.objects.get(admin=self.student_user)
        self.student.course = self.course_a
        self.student.session = self.session
        self.student.save()

        self.result = StudentResult.objects.create(
            student=self.student, subject=self.subject_a, test=10, exam=20)

    def login(self, user):
        self.client.force_login(user)

    # Issue: class-based view bypasses middleware
    def test_student_cannot_edit_results(self):
        self.login(self.student_user)
        response = self.client.post(reverse('edit_student_result'), {
            'session_year': self.session.id, 'subject': self.subject_a.id,
            'student': self.student.id, 'test': 39, 'exam': 59})
        self.assertNotEqual(response.status_code, 200)
        self.result.refresh_from_db()
        self.assertEqual((self.result.test, self.result.exam), (10, 20))

    def test_student_redirected_from_edit_result_page(self):
        self.login(self.student_user)
        response = self.client.get(reverse('edit_student_result'))
        self.assertRedirects(response, reverse('student_home'),
                             fetch_redirect_response=False)

    def test_other_teacher_cannot_edit_results(self):
        self.login(self.teacher_b)
        self.client.post(reverse('edit_student_result'), {
            'session_year': self.session.id, 'subject': self.subject_a.id,
            'student': self.student.id, 'test': 39, 'exam': 59})
        self.result.refresh_from_db()
        self.assertEqual((self.result.test, self.result.exam), (10, 20))

    def test_owning_teacher_can_edit_results(self):
        self.login(self.teacher_a)
        response = self.client.post(reverse('edit_student_result'), {
            'session_year': self.session.id, 'subject': self.subject_a.id,
            'student': self.student.id, 'test': 39, 'exam': 59})
        self.assertRedirects(response, reverse('edit_student_result'),
                             fetch_redirect_response=False)
        self.result.refresh_from_db()
        self.assertEqual((self.result.test, self.result.exam), (39, 59))

    # Issue: teacher can modify grades across subjects
    def test_other_teacher_cannot_add_result(self):
        self.login(self.teacher_b)
        self.client.post(reverse('staff_add_result'), {
            'student_list': self.student.id, 'subject': self.subject_a.id,
            'test': 37, 'exam': 38})
        self.result.refresh_from_db()
        self.assertEqual((self.result.test, self.result.exam), (10, 20))

    def test_other_teacher_cannot_create_result(self):
        self.result.delete()
        self.login(self.teacher_b)
        self.client.post(reverse('staff_add_result'), {
            'student_list': self.student.id, 'subject': self.subject_a.id,
            'test': 37, 'exam': 38})
        self.assertFalse(StudentResult.objects.exists())

    def test_owning_teacher_can_add_result(self):
        self.login(self.teacher_a)
        self.client.post(reverse('staff_add_result'), {
            'student_list': self.student.id, 'subject': self.subject_a.id,
            'test': 37, 'exam': 38})
        self.result.refresh_from_db()
        self.assertEqual((self.result.test, self.result.exam), (37, 38))

    def test_out_of_range_scores_rejected(self):
        self.login(self.teacher_a)
        self.client.post(reverse('staff_add_result'), {
            'student_list': self.student.id, 'subject': self.subject_a.id,
            'test': 1000, 'exam': -5})
        self.result.refresh_from_db()
        self.assertEqual((self.result.test, self.result.exam), (10, 20))

    def test_other_teacher_cannot_fetch_result(self):
        self.login(self.teacher_b)
        response = self.client.post(reverse('fetch_student_result'), {
            'subject': self.subject_a.id, 'student': self.student.id})
        self.assertEqual(response.content, b'False')

    # Issue: IDOR in attendance endpoints
    def test_other_teacher_cannot_list_students(self):
        self.login(self.teacher_b)
        response = self.client.post(reverse('get_students'), {
            'subject': self.subject_a.id, 'session': self.session.id})
        self.assertEqual(response.status_code, 404)

    def test_owning_teacher_can_list_students(self):
        self.login(self.teacher_a)
        response = self.client.post(reverse('get_students'), {
            'subject': self.subject_a.id, 'session': self.session.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(json.loads(response.content))[0]['id'],
                         self.student.id)

    def test_other_teacher_cannot_save_attendance(self):
        self.login(self.teacher_b)
        response = self.client.post(reverse('save_attendance'), {
            'student_ids': json.dumps([{'id': self.student.id, 'status': 1}]),
            'date': '2026-10-25', 'subject': self.subject_a.id,
            'session': self.session.id})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Attendance.objects.exists())

    def test_teacher_cannot_mark_student_outside_subject(self):
        other = make_user('stu.b@example.com', 3)
        other_student = Student.objects.get(admin=other)
        other_student.course = self.course_b
        other_student.session = self.session
        other_student.save()
        self.login(self.teacher_a)
        response = self.client.post(reverse('save_attendance'), {
            'student_ids': json.dumps([{'id': other_student.id, 'status': 1}]),
            'date': '2026-10-25', 'subject': self.subject_a.id,
            'session': self.session.id})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AttendanceReport.objects.exists())
        self.assertFalse(Attendance.objects.exists())

    def test_owning_teacher_can_save_attendance(self):
        self.login(self.teacher_a)
        response = self.client.post(reverse('save_attendance'), {
            'student_ids': json.dumps([{'id': self.student.id, 'status': 1}]),
            'date': '2026-10-25', 'subject': self.subject_a.id,
            'session': self.session.id})
        self.assertEqual(response.content, b'OK')
        self.assertTrue(AttendanceReport.objects.get(student=self.student).status)

    def _attendance_for_a(self):
        attendance = Attendance.objects.create(
            session=self.session, subject=self.subject_a, date='2026-10-25')
        AttendanceReport.objects.create(
            student=self.student, attendance=attendance, status=True)
        return attendance

    def test_other_teacher_cannot_read_attendance(self):
        attendance = self._attendance_for_a()
        self.login(self.teacher_b)
        response = self.client.post(reverse('get_student_attendance'), {
            'attendance_date_id': attendance.id})
        self.assertEqual(response.status_code, 404)
        response = self.client.post(reverse('get_attendance'), {
            'subject': self.subject_a.id, 'session': self.session.id})
        self.assertEqual(response.status_code, 404)

    def test_student_cannot_list_attendance_dates(self):
        self._attendance_for_a()
        self.login(self.student_user)
        response = self.client.post(reverse('get_attendance'), {
            'subject': self.subject_a.id, 'session': self.session.id})
        self.assertEqual(response.status_code, 403)

    def test_update_attendance_route_reachable_and_scoped(self):
        attendance = self._attendance_for_a()
        self.assertNotEqual(reverse('update_attendance'),
                            reverse('staff_update_attendance'))
        payload = {'date': attendance.id, 'student_ids': json.dumps(
            [{'id': self.student_user.id, 'status': 0}])}

        self.login(self.teacher_b)
        response = self.client.post(reverse('update_attendance'), payload)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(AttendanceReport.objects.get().status)

        self.login(self.teacher_a)
        response = self.client.post(reverse('update_attendance'), payload)
        self.assertEqual(response.content, b'OK')
        self.assertFalse(AttendanceReport.objects.get().status)

    def test_attendance_endpoints_require_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher_a)
        response = client.post(reverse('save_attendance'), {
            'student_ids': json.dumps([{'id': self.student.id, 'status': 1}]),
            'date': '2026-10-25', 'subject': self.subject_a.id,
            'session': self.session.id})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Attendance.objects.exists())

    def test_hod_can_list_attendance_dates(self):
        self._attendance_for_a()
        hod = make_user('hod@example.com', 1)
        self.login(hod)
        response = self.client.post(reverse('get_attendance'), {
            'subject': self.subject_a.id, 'session': self.session.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(json.loads(response.content))), 1)


class HodSecurityTests(TestCase):
    def setUp(self):
        self.hod = make_user('hod@example.com', 1)
        self.client.force_login(self.hod)
        self.course = Course.objects.create(name='CSE')

    def test_delete_requires_post(self):
        response = self.client.get(reverse('delete_course', args=[self.course.id]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Course.objects.filter(id=self.course.id).exists())

        response = self.client.post(reverse('delete_course', args=[self.course.id]))
        self.assertRedirects(response, reverse('manage_course'),
                             fetch_redirect_response=False)
        self.assertFalse(Course.objects.filter(id=self.course.id).exists())

    def test_delete_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.hod)
        response = client.post(reverse('delete_course', args=[self.course.id]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Course.objects.filter(id=self.course.id).exists())

    def test_staff_cannot_reach_hod_views(self):
        teacher = make_user('staff@example.com', 2)
        self.client.force_login(teacher)
        response = self.client.post(reverse('delete_course', args=[self.course.id]))
        self.assertRedirects(response, reverse('staff_home'),
                             fetch_redirect_response=False)
        self.assertTrue(Course.objects.filter(id=self.course.id).exists())

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('manage_course'))
        self.assertRedirects(response, reverse('login_page'),
                             fetch_redirect_response=False)

    def test_notification_saved_without_fcm_key(self):
        student_user = make_user('stu@example.com', 3)
        with self.settings(FCM_SERVER_KEY=''):
            response = self.client.post(reverse('send_student_notification'), {
                'id': student_user.id, 'message': 'Hello'})
        self.assertEqual(response.content, b'True')
        self.assertEqual(Student.objects.get(admin=student_user)
                         .notificationstudent_set.get().message, 'Hello')

    def test_no_hardcoded_secrets_in_source(self):
        import pathlib
        app_dir = pathlib.Path(__file__).resolve().parent
        source = ''.join(p.read_text() for p in app_dir.glob('*.py')
                         if p.name != 'tests.py')
        self.assertNotIn('AAAA3Bm8j_M', source)
        self.assertNotIn('6LfswtgZAAAAABX9gbLqe', source)


class QueryCountTests(TestCase):
    """Dashboards and listings must not issue one query per row (N+1)."""

    def setUp(self):
        self.session = Session.objects.create(
            start_year=datetime.date(2026, 1, 1), end_year=datetime.date(2026, 12, 31))
        self.course = Course.objects.create(name='CSE')
        self.hod = make_user('hod@example.com', 1)
        self.teacher = make_user('staff@example.com', 2)
        self.staff = Staff.objects.get(admin=self.teacher)
        self.staff.course = self.course
        self.staff.save()
        self.student_user = make_user('stu@example.com', 3)
        self.student = Student.objects.get(admin=self.student_user)
        self.student.course = self.course
        self.student.session = self.session
        self.student.save()

    def add_rows(self, count):
        start = Subject.objects.count()
        for i in range(start, start + count):
            subject = Subject.objects.create(
                name='Subject %d' % i, staff=self.staff, course=self.course)
            attendance = Attendance.objects.create(
                session=self.session, subject=subject, date='2026-10-%02d' % (i + 1))
            AttendanceReport.objects.create(
                student=self.student, attendance=attendance, status=bool(i % 2))
            StudentResult.objects.create(
                student=self.student, subject=subject, test=10, exam=20)
            user = make_user('extra%d@example.com' % i, 3)
            extra = Student.objects.get(admin=user)
            extra.course = self.course
            extra.save()

    def assertConstantQueries(self, user, url_name):
        self.client.force_login(user)
        url = reverse(url_name)
        self.add_rows(1)
        with CaptureQueries() as small:
            self.assertEqual(self.client.get(url).status_code, 200)
        self.add_rows(5)
        with CaptureQueries() as large:
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(len(small), len(large),
                         '%s query count grows with rows' % url_name)

    def test_admin_home(self):
        self.assertConstantQueries(self.hod, 'admin_home')

    def test_manage_student(self):
        self.assertConstantQueries(self.hod, 'manage_student')

    def test_manage_subject(self):
        self.assertConstantQueries(self.hod, 'manage_subject')

    def test_staff_home(self):
        self.assertConstantQueries(self.teacher, 'staff_home')

    def test_student_home(self):
        self.assertConstantQueries(self.student_user, 'student_home')

    def test_student_view_result(self):
        self.assertConstantQueries(self.student_user, 'student_view_result')

    def test_student_home_counts(self):
        self.add_rows(4)
        self.client.force_login(self.student_user)
        context = self.client.get(reverse('student_home')).context
        self.assertEqual(context['total_attendance'], 4)
        self.assertEqual(context['data_present'], [0, 1, 0, 1])
        self.assertEqual(context['data_absent'], [1, 0, 1, 0])
        self.assertEqual(context['percent_present'], 50)


class LogoutTests(TestCase):
    def setUp(self):
        self.user = make_user('stu@example.com', 3)
        self.client.force_login(self.user)

    def test_logout_requires_post(self):
        response = self.client.get(reverse('user_logout'))
        self.assertEqual(response.status_code, 405)
        self.assertIn('_auth_user_id', self.client.session)

    def test_logout_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(reverse('user_logout'))
        self.assertEqual(response.status_code, 403)
        self.assertIn('_auth_user_id', client.session)

    def test_post_logs_out(self):
        response = self.client.post(reverse('user_logout'))
        self.assertRedirects(response, '/', fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_logout_rendered_as_form(self):
        content = self.client.get(reverse('student_home')).content.decode()
        self.assertIn('action="%s"' % reverse('user_logout'), content)
        self.assertNotIn('href="%s"' % reverse('user_logout'), content)

    def test_unknown_role_is_logged_out(self):
        CustomUser.objects.filter(pk=self.user.pk).update(user_type='9')
        response = self.client.get(reverse('student_home'))
        self.assertRedirects(response, reverse('login_page'),
                             fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)


import tempfile
from django.contrib.auth import authenticate
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from .models import (FeedbackStaff, FeedbackStudent, LeaveReportStaff,
                     LeaveReportStudent, LeaveStatus, NotificationStaff)
from .services import ProfileImageStorage
from .staff_views import parse_scores


class RefactoredBehaviorTests(TestCase):
    """Unit and integration tests covering refactored domain models, services, and bug fixes."""

    TINY_GIF = (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
        b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
        b"\x00\x02\x02D\x01\x00;"
    )

    def setUp(self):
        self.session = Session.objects.create(
            start_year=datetime.date(2026, 1, 1), end_year=datetime.date(2026, 12, 31)
        )
        self.course = Course.objects.create(name="CS")
        self.hod_user = make_user("hod.ref@example.com", "1")
        self.staff_user = make_user("staff.ref@example.com", "2")
        self.staff_user.first_name = "Alan"
        self.staff_user.last_name = "Turing"
        self.staff_user.fcm_token = "tok-123"
        self.staff_user.save()
        self.staff = Staff.objects.get(admin=self.staff_user)
        self.staff.course = self.course
        self.staff.save()

        self.student_user = make_user("stu.ref@example.com", 3)
        self.student_user.first_name = "Ada"
        self.student_user.last_name = "Lovelace"
        self.student_user.save()
        self.student = Student.objects.get(admin=self.student_user)
        self.student.course = self.course
        self.student.session = self.session
        self.student.save()

        self.subject = Subject.objects.create(
            name="Algorithms", staff=self.staff, course=self.course
        )

    def test_post_save_signal_handles_int_and_str_user_types_and_db_reload(self):
        self.assertTrue(Staff.objects.filter(admin=self.staff_user).exists())
        self.assertTrue(Student.objects.filter(admin=self.student_user).exists())

        reloaded = CustomUser.objects.get(pk=self.staff_user.pk)
        self.assertEqual(reloaded.user_type, "2")
        reloaded.first_name = "Grace"
        reloaded.save()
        self.assertEqual(CustomUser.objects.get(pk=reloaded.pk).first_name, "Grace")

    def test_email_backend_via_django_authenticate_and_dologin_redirects(self):
        with self.settings(RECAPTCHA_SECRET_KEY=""):
            authed = authenticate(
                None, username=self.staff_user.email, password="Passw0rd!"
            )
            self.assertEqual(authed, self.staff_user)

            for user, target in [
                (self.hod_user, "admin_home"),
                (self.staff_user, "staff_home"),
                (self.student_user, "student_home"),
            ]:
                self.client.logout()
                response = self.client.post(
                    reverse("user_login"),
                    {"email": user.email, "password": "Passw0rd!"},
                )
                self.assertRedirects(
                    response, reverse(target), fetch_redirect_response=False
                )

    def test_hide_delegate_properties_on_profiles_and_attendance_report(self):
        attendance = Attendance.objects.create(
            session=self.session, subject=self.subject, date=datetime.date(2026, 10, 1)
        )
        report = AttendanceReport.objects.create(
            student=self.student, attendance=attendance, status=True
        )
        self.assertEqual(self.staff.full_name, "Turing Alan")
        self.assertEqual(self.staff.email, self.staff_user.email)
        self.assertEqual(self.staff.fcm_token, "tok-123")
        self.assertEqual(self.staff.user_id, self.staff_user.id)
        self.assertEqual(report.student_full_name, "Lovelace Ada")
        self.assertEqual(report.student_user_id, self.student_user.id)

    def test_leave_apply_decision_and_feedback_save_reply(self):
        leave = LeaveReportStaff.objects.create(
            staff=self.staff, date="2026-10-10", message="Medical"
        )
        self.assertEqual(leave.status, LeaveStatus.PENDING)
        leave.apply_decision("1")
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveStatus.APPROVED)
        leave.apply_decision("invalid")
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveStatus.REJECTED)

        fb = FeedbackStaff.objects.create(staff=self.staff, feedback="Need projector")
        fb.save_reply("Approved")
        fb.refresh_from_db()
        self.assertEqual(fb.reply, "Approved")

    def test_parse_scores_bounds_and_validation(self):
        self.assertEqual(parse_scores("35", "55.5"), (35.0, 55.5))
        for bad_test, bad_exam in [(-1, 10), (41, 10), (10, -5), (10, 61), ("x", 10)]:
            with self.assertRaises(ValueError):
                parse_scores(bad_test, bad_exam)

    def test_student_view_attendance_invalid_input_returns_400(self):
        self.client.force_login(self.student_user)
        response = self.client.post(
            reverse("student_view_attendance"),
            {"subject": self.subject.id, "start_date": "bad-date", "end_date": "2026-10-31"},
        )
        self.assertEqual(response.status_code, 400)

        attendance = Attendance.objects.create(
            session=self.session, subject=self.subject, date=datetime.date(2026, 10, 15)
        )
        AttendanceReport.objects.create(
            student=self.student, attendance=attendance, status=True
        )
        ok_resp = self.client.post(
            reverse("student_view_attendance"),
            {
                "subject": self.subject.id,
                "start_date": "2026-10-01",
                "end_date": "2026-10-31",
            },
        )
        self.assertEqual(ok_resp.status_code, 200)
        parsed = json.loads(json.loads(ok_resp.content))
        self.assertEqual(parsed, [{"date": "2026-10-15", "status": True}])

    def test_profile_image_storage_and_hod_crud_workflows(self):
        self.client.force_login(self.hod_user)
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=tmpdir):
                upload = SimpleUploadedFile(
                    "avatar.gif", self.TINY_GIF, content_type="image/gif"
                )
                url = ProfileImageStorage().save_and_get_url(upload)
                self.assertTrue(url.startswith("/media/avatar"))
                self.assertIsNone(ProfileImageStorage().save_and_get_url(None))

                staff_pic = SimpleUploadedFile(
                    "staff.gif", self.TINY_GIF, content_type="image/gif"
                )
                add_resp = self.client.post(
                    reverse("add_staff"),
                    {
                        "first_name": "New",
                        "last_name": "Teacher",
                        "email": "new.teacher@example.com",
                        "gender": "F",
                        "password": "Passw0rd!",
                        "address": "123 Lane",
                        "course": self.course.id,
                        "profile_pic": staff_pic,
                    },
                )
                self.assertRedirects(
                    add_resp, reverse("add_staff"), fetch_redirect_response=False
                )
                created_staff = Staff.objects.get(admin__email="new.teacher@example.com")
                edit_resp = self.client.post(
                    reverse("edit_staff", args=[created_staff.id]),
                    {
                        "first_name": "Updated",
                        "last_name": "Teacher",
                        "email": "new.teacher@example.com",
                        "gender": "F",
                        "password": "",
                        "address": "456 Road",
                        "course": self.course.id,
                    },
                )
                self.assertRedirects(
                    edit_resp,
                    reverse("edit_staff", args=[created_staff.id]),
                    fetch_redirect_response=False,
                )
                created_staff.admin.refresh_from_db()
                self.assertEqual(created_staff.admin.first_name, "Updated")

        course_resp = self.client.post(reverse("add_course"), {"name": "Math"})
        self.assertRedirects(
            course_resp, reverse("add_course"), fetch_redirect_response=False
        )
        math_course = Course.objects.get(name="Math")
        edit_course_resp = self.client.post(
            reverse("edit_course", args=[math_course.id]), {"name": "Applied Math"}
        )
        self.assertRedirects(
            edit_course_resp,
            reverse("edit_course", args=[math_course.id]),
            fetch_redirect_response=False,
        )
        math_course.refresh_from_db()
        self.assertEqual(math_course.name, "Applied Math")

    def test_leave_and_feedback_workflows_end_to_end(self):
        self.client.force_login(self.staff_user)
        self.client.post(
            reverse("staff_apply_leave"), {"date": "2026-11-01", "message": "Conference"}
        )
        self.client.post(reverse("staff_feedback"), {"feedback": "Lab upgrade"})
        staff_leave = LeaveReportStaff.objects.get(staff=self.staff)
        staff_fb = FeedbackStaff.objects.get(staff=self.staff)

        self.client.force_login(self.student_user)
        self.client.post(
            reverse("student_apply_leave"), {"date": "2026-11-02", "message": "Family"}
        )
        self.client.post(reverse("student_feedback"), {"feedback": "More tutorials"})
        stu_leave = LeaveReportStudent.objects.get(student=self.student)
        stu_fb = FeedbackStudent.objects.get(student=self.student)

        self.client.force_login(self.hod_user)
        self.assertEqual(
            self.client.post(
                reverse("view_staff_leave"), {"id": staff_leave.id, "status": "1"}
            ).content,
            b"True",
        )
        self.assertEqual(
            self.client.post(
                reverse("view_student_leave"), {"id": stu_leave.id, "status": "-1"}
            ).content,
            b"True",
        )
        self.assertEqual(
            self.client.post(
                reverse("staff_feedback_message"), {"id": staff_fb.id, "reply": "Done"}
            ).content,
            b"True",
        )
        self.assertEqual(
            self.client.post(
                reverse("student_feedback_message"), {"id": stu_fb.id, "reply": "Noted"}
            ).content,
            b"True",
        )

        staff_leave.refresh_from_db()
        stu_leave.refresh_from_db()
        staff_fb.refresh_from_db()
        stu_fb.refresh_from_db()
        self.assertEqual(staff_leave.status, LeaveStatus.APPROVED)
        self.assertEqual(stu_leave.status, LeaveStatus.REJECTED)
        self.assertEqual(staff_fb.reply, "Done")
        self.assertEqual(stu_fb.reply, "Noted")

    def test_consolidated_templates_and_fixed_html_render(self):
        self.client.logout()
        login_html = self.client.get(reverse("login_page")).content.decode()
        self.assertIn(
            "https://code.ionicframework.com/ionicons/2.0.1/css/ionicons.min.css",
            login_html,
        )
        self.assertNotIn("/static/https:", login_html)

        LeaveReportStaff.objects.create(
            staff=self.staff, date="2026-11-05", message="Sick", status=LeaveStatus.APPROVED
        )
        self.client.force_login(self.hod_user)
        leave_html = self.client.get(reverse("view_staff_leave")).content.decode()
        self.assertIn("Approved", leave_html)

        for user, url_name in [
            (self.hod_user, "admin_view_profile"),
            (self.staff_user, "staff_view_profile"),
            (self.staff_user, "staff_view_notification"),
            (self.student_user, "student_view_profile"),
            (self.student_user, "student_view_notification"),
        ]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse(url_name)).status_code, 200)

        with self.settings(FCM_SERVER_KEY=""):
            self.client.force_login(self.hod_user)
            resp = self.client.post(
                reverse("send_staff_notification"),
                {"id": self.staff_user.id, "message": "Meeting at 3pm"},
            )
            self.assertEqual(resp.content, b"True")
            self.assertTrue(
                NotificationStaff.objects.filter(
                    staff=self.staff, message="Meeting at 3pm"
                ).exists()
            )
