from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.views.decorators.http import require_POST

from .models import Attendance, Session, Staff, Subject, UserRole
from .services import AttendanceService, CaptchaService, legacy_json_response


def login_page(request):
    if request.user.is_authenticated:
        dashboard = request.user.get_dashboard_url_name()
        if dashboard:
            return redirect(reverse(dashboard))
    return render(request, "main_app/login.html")


def doLogin(request):
    if request.method != "POST":
        return HttpResponse("<h4>Denied</h4>")

    captcha_valid, captcha_error = CaptchaService.verify(request)
    if not captcha_valid:
        messages.error(request, captcha_error)
        return redirect("/")

    user = authenticate(
        request,
        username=request.POST.get("email"),
        password=request.POST.get("password"),
    )
    if user is None:
        messages.error(request, "Invalid details")
        return redirect("/")

    login(request, user)
    return redirect(reverse(user.get_dashboard_url_name()))


@require_POST
def logout_user(request):
    logout(request)
    return redirect("/")


@require_POST
def get_attendance(request):
    # Shared by HOD (any subject) and staff (only subjects they teach).
    user = request.user
    role = UserRole.normalize(getattr(user, "user_type", ""))
    if not user.is_authenticated or role not in (UserRole.HOD, UserRole.STAFF):
        raise PermissionDenied

    subject_id = request.POST.get("subject")
    session_id = request.POST.get("session")
    if role == UserRole.STAFF:
        staff = get_object_or_404(Staff, admin=user)
        subject = get_object_or_404(Subject, id=subject_id, staff=staff)
    else:
        subject = get_object_or_404(Subject, id=subject_id)

    session = get_object_or_404(Session, id=session_id)
    attendance = Attendance.objects.filter(subject=subject, session=session)
    return legacy_json_response(AttendanceService.serialize_attendance_dates(attendance))


def showFirebaseJS(request):
    data = """
    // Give the service worker access to Firebase Messaging.
// Note that you can only use Firebase Messaging here, other Firebase libraries
// are not available in the service worker.
importScripts('https://www.gstatic.com/firebasejs/7.22.1/firebase-app.js');
importScripts('https://www.gstatic.com/firebasejs/7.22.1/firebase-messaging.js');

// Initialize the Firebase app in the service worker by passing in
// your app's Firebase config object.
// https://firebase.google.com/docs/web/setup#config-object
firebase.initializeApp({
    apiKey: "AIzaSyBarDWWHTfTMSrtc5Lj3Cdw5dEvjAkFwtM",
    authDomain: "sms-with-django.firebaseapp.com",
    databaseURL: "https://sms-with-django.firebaseio.com",
    projectId: "sms-with-django",
    storageBucket: "sms-with-django.appspot.com",
    messagingSenderId: "945324593139",
    appId: "1:945324593139:web:03fa99a8854bbd38420c86",
    measurementId: "G-2F2RXTL9GT"
});

// Retrieve an instance of Firebase Messaging so that it can handle background
// messages.
const messaging = firebase.messaging();
messaging.setBackgroundMessageHandler(function (payload) {
    const notification = JSON.parse(payload);
    const notificationOption = {
        body: notification.body,
        icon: notification.icon
    }
    return self.registration.showNotification(payload.notification.title, notificationOption);
});
    """
    return HttpResponse(data, content_type="application/javascript")
